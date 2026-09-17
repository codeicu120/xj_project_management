from datetime import date
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Count
from django.http import FileResponse, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST
from .models import *
from .forms import *
from .services import allowed, has_role, can_process, process, act_bug, audit, notify, attach, snapshot, validate_file

def projects_for(user):
    qs=Project.objects.all()
    return qs if user.is_superuser else qs.filter(Q(owner=user)|Q(memberships__user=user)).distinct()
def req_for(user): return Requirement.objects.filter(project__in=projects_for(user)).select_related('owner','project')
def base(request): return {'nav_projects':projects_for(request.user),'unread':Notification.objects.filter(user=request.user,read=False).count()}
def page(request,template,**context):
    query=request.GET.copy(); query.pop('page',None)
    return render(request,'tracker/'+template,{**base(request),'page_query':query.urlencode()+'&' if query else '',**context})
def project_access(user,project):
    if not allowed(user,project): raise PermissionDenied

def filter_requirements(request,qs):
    for field in ['project','stage','owner','priority']:
        value=request.GET.get(field)
        if value:
            if field in ['project','owner'] and not value.isdigit(): continue
            qs=qs.filter(**{field:value})
    if request.GET.get('q'): qs=qs.filter(Q(title__icontains=request.GET['q'])|Q(number__icontains=request.GET['q'])|Q(body__icontains=request.GET['q']))
    for param,lookup in [('start','updated_at__date__gte'),('end','updated_at__date__lte')]:
        try:
            if request.GET.get(param): qs=qs.filter(**{lookup:date.fromisoformat(request.GET[param])})
        except ValueError: pass
    return qs
@login_required
def dashboard(request):
    qs=req_for(request.user); bugs=Bug.objects.filter(requirement__in=qs)
    return page(request,'dashboard.html',active='dashboard',total=qs.count(),pending=qs.filter(owner=request.user).exclude(stage='10').count(),overdue=qs.exclude(stage='10').filter(due_date__lt=timezone.localdate()).count(),open_bugs=bugs.exclude(status='closed').count(),todo=qs.filter(owner=request.user).exclude(stage='10')[:7],recent=Audit.objects.filter(project__in=projects_for(request.user)).select_related('actor')[:8],overdue_items=qs.exclude(stage='10').filter(due_date__lt=timezone.localdate())[:5],projects=projects_for(request.user)[:4])
@login_required
def projects(request): return page(request,'projects.html',active='projects',projects=projects_for(request.user))
@login_required
def project_create(request):
    if not (request.user.is_staff or request.user.is_superuser): raise PermissionDenied
    form=ProjectForm(request.POST or None)
    if request.method=='POST' and form.is_valid():
        with transaction.atomic():
            project=form.save()
            for user in form.cleaned_data['members']: Membership.objects.get_or_create(project=project,user=user,role='需求提起人')
            for role,_ in ROLES: Membership.objects.get_or_create(project=project,user=project.owner,role=role)
            audit(request.user,project,'创建项目',project.name)
        return redirect('project_detail',pk=project.pk)
    return page(request,'form.html',active='projects',title='创建项目',subtitle='建立项目空间，集中管理需求与交付。',form=form)
@login_required
def project_detail(request,pk):
    project=get_object_or_404(projects_for(request.user),pk=pk)
    return page(request,'project.html',active='projects',project=project,requirements=project.requirements.select_related('owner'),memberships=project.memberships.select_related('user'))
@login_required
def requirements(request):
    qs=filter_requirements(request,req_for(request.user))
    return page(request,'requirements.html',active='requirements',items=Paginator(qs,20).get_page(request.GET.get('page')),stages=STAGES,priorities=PRIORITIES,people=User.objects.filter(Q(membership__project__in=projects_for(request.user))|Q(project__in=projects_for(request.user))).distinct())
@login_required
def requirement_create(request):
    ps=projects_for(request.user).filter(archived=False); pid=request.POST.get('project') or request.GET.get('project'); project=ps.filter(pk=pid).first() if pid and pid.isdigit() else ps.first()
    form=RequirementForm(request.POST or None,projects=ps,project=project,initial={'project':project,'owner':request.user})
    if request.method=='POST' and form.is_valid():
        if not has_role(form.cleaned_data['owner'],form.cleaned_data['project'],['需求提起人']): form.add_error('owner','负责人需要具备需求提起人角色。')
        else:
            with transaction.atomic():
                req=form.save(commit=False); req.creator=request.user; req.save()
                Record.objects.create(requirement=req,stage='0',round=1,actor=request.user,owner=req.owner,action='创建需求',note=req.body,snapshot=snapshot(req),incomplete=True)
                audit(request.user,req.project,'创建需求',str(req)); notify(req.owner,f'新需求：{req.title}',f'/requirements/{req.pk}/')
            messages.success(request,'需求已创建。可在处理窗口补充资料。'); return redirect('requirement_detail',pk=req.pk)
    return page(request,'form.html',active='requirements',title='提出新需求',subtitle='描述业务目标、处理规则和验收标准。选择项目后将刷新可选负责人。',form=form,project_selector=True)
@login_required
def requirement_detail(request,pk):
    req=get_object_or_404(req_for(request.user),pk=pk); form=ProcessForm(request.POST or None,request.FILES or None,requirement=req)
    if request.method=='POST' and form.is_valid():
        try:
            rec=process(request.user,pk,form.cleaned_data)
            messages.success(request,'处理已保存，历史记录已归档。')
            if rec.incomplete: messages.warning(request,'本次未上传材料，已标记资料不完整，仍可继续流程。')
            return redirect('requirement_detail',pk=pk)
        except ValidationError as e: form.add_error(None,e)
    return page(request,'requirement.html',active='requirements',req=req,form=form,can_process=can_process(request.user,req) and not req.project.archived,stages=STAGES,records=req.records.select_related('actor','owner','receiver'),attachments=req.attachments.select_related('actor'),bugs=req.bugs.select_related('owner'),open_count=req.bugs.exclude(status='closed').count())
@login_required
def bugs(request):
    qs=Bug.objects.filter(requirement__in=req_for(request.user)).select_related('requirement__project','owner')
    for param,field in [('status','status'),('severity','severity'),('priority','priority'),('discovered_version','discovered_version__icontains'),('fix_version','fix_version__icontains'),('project','requirement__project_id'),('owner','owner_id')]:
        value=request.GET.get(param)
        if value and (param not in ['project','owner'] or value.isdigit()): qs=qs.filter(**{field:value})
    if request.GET.get('q'): qs=qs.filter(Q(title__icontains=request.GET['q'])|Q(number__icontains=request.GET['q']))
    return page(request,'bugs.html',active='bugs',items=Paginator(qs,20).get_page(request.GET.get('page')),statuses=Bug.STATES,severities=Bug._meta.get_field('severity').choices,priorities=PRIORITIES)
@login_required
def bug_create(request,pk):
    req=get_object_or_404(req_for(request.user),pk=pk)
    if not has_role(request.user,req.project,['测试负责人']): raise PermissionDenied
    if req.stage!='5' or req.project.archived:
        messages.error(request,'只能在测试验收环节登记 Bug。'); return redirect('requirement_detail',pk=pk)
    form=BugForm(request.POST or None,request.FILES or None,project=req.project)
    if request.method=='POST' and form.is_valid():
        try: validate_file(form.cleaned_data.get('file'))
        except ValidationError as e: form.add_error('file',e)
        if not form.errors:
            with transaction.atomic():
                req=Requirement.objects.select_for_update().get(pk=pk)
                if req.stage!='5': raise PermissionDenied
                record=Record.objects.create(requirement=req,stage='5',round=req.round,actor=request.user,owner=req.owner,action='测试发现 Bug',note=form.cleaned_data['title'],version=form.cleaned_data['discovered_version'],snapshot=snapshot(req),incomplete=not bool(form.cleaned_data.get('file')))
                bug=form.save(commit=False); bug.requirement=req; bug.round=req.round; bug.test_record=record; bug.save()
                event=BugEvent.objects.create(bug=bug,actor=request.user,action='创建 Bug',note=bug.steps,version=bug.discovered_version,previous_owner=bug.owner,receiver=bug.owner)
                attach(request.user,req,form.cleaned_data.get('file'),record=record,event=event)
                audit(request.user,req.project,'创建 Bug',str(bug)); notify(bug.owner,str(bug),f'/bugs/{bug.pk}/')
            return redirect('bug_detail',pk=bug.pk)
    return page(request,'form.html',active='bugs',title='登记 Bug',subtitle=f'{req.number} · 第 {req.round} 轮测试',form=form)
@login_required
def bug_detail(request,pk):
    bug=get_object_or_404(Bug.objects.select_related('requirement__project','owner'),pk=pk); project_access(request.user,bug.requirement.project)
    form=BugActionForm(request.POST or None,request.FILES or None,bug=bug)
    if request.method=='POST' and form.is_valid():
        try:
            act_bug(request.user,pk,form.cleaned_data); messages.success(request,'Bug 处理记录已保存。'); return redirect('bug_detail',pk=pk)
        except ValidationError as e: form.add_error(None,e)
    return page(request,'bug.html',active='bugs',bug=bug,form=form,events=bug.events.select_related('actor','previous_owner','receiver').prefetch_related('attachments'))
@login_required
def attachment(request,pk):
    item=get_object_or_404(Attachment.objects.select_related('requirement__project'),pk=pk); project_access(request.user,item.requirement.project)
    try: response=FileResponse(item.file.open('rb'),as_attachment=True,filename=item.name,content_type='application/octet-stream')
    except FileNotFoundError: raise Http404('附件文件不存在')
    response['X-Content-Type-Options']='nosniff'; audit(request.user,item.requirement.project,'下载附件',item.name); return response
@login_required
def notifications(request): return page(request,'notifications.html',active='notifications',items=Notification.objects.filter(user=request.user)[:100])
@login_required
@require_POST
def notifications_read(request):
    Notification.objects.filter(user=request.user,read=False).update(read=True); return redirect('notifications')
@login_required
def logs(request): return page(request,'logs.html',active='logs',items=Paginator(Audit.objects.filter(project__in=projects_for(request.user)).select_related('actor','project'),30).get_page(request.GET.get('page')))
