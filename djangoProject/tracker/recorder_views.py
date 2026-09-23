import hashlib
import json
import secrets
from datetime import date, timedelta
from decimal import Decimal
from django.contrib import messages, admin
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction, IntegrityError
from django.db.models import Q, Sum
from django.http import JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from .models import *
from .recorder_forms import *
from .recorder_services import *


def projects_for(user):
    qs=Project.objects.all()
    return qs if user.is_superuser else qs.filter(Q(owner=user)|Q(memberships__user=user)).distinct()
def writable_projects(user):
    qs=projects_for(user).filter(archived=False)
    return qs if user.is_superuser else qs.filter(memberships__user=user,memberships__role='记录人员').distinct()
def selected_projects(request):
    qs=projects_for(request.user); value=request.GET.get('project','')
    if value:
        if not value.isdigit(): return qs.none()
        qs=qs.filter(pk=value)
    return qs

def page(request,template,**context):
    ps=projects_for(request.user)
    return render(request,'recorder/'+template,{'projects':ps,'nav_projects':ps,'selected_project':request.GET.get('project',''),'can_write':writable_projects(request.user).filter(pk__in=selected_projects(request)).exists(),'can_access_admin':bool(admin.site.get_app_list(request)) if request.user.is_staff and request.user.is_active else False,**context})
def reqs(request): return Requirement.objects.filter(project__in=selected_projects(request)).select_related('project','creator')
def error(form,e):
    if hasattr(e,'message_dict'):
        for key,values in e.message_dict.items():
            for msg in values: form.add_error(key if key in form.fields else None,msg)
    else: form.add_error(None,e)

@login_required
def home(request):
    requirements=list(reqs(request)); decorated=[{'req':r,'evidence':evidence(r)} for r in requirements]
    missing=[r for r in decorated if r['evidence']['missing']]
    ps=selected_projects(request); today=timezone.localdate()
    due=Asset.objects.filter(project__in=ps,expires_on__lte=today+timedelta(days=30)).order_by('expires_on')
    return page(request,'home.html',active='home',title='工作总览',requirements=decorated[:8],missing=missing,ongoing=sum(r.stage!='10' for r in requirements),pending_data=DailyData.objects.filter(project__in=ps).exclude(status='verified').count(),due=due,recent=Audit.objects.filter(project__in=ps).select_related('actor')[:8])
@login_required
def requirement_list(request):
    qs=reqs(request); q=request.GET.get('q',''); stage=request.GET.get('stage','')
    if q: qs=qs.filter(Q(title__icontains=q)|Q(number__icontains=q)|Q(business_owner__icontains=q))
    if stage: qs=qs.filter(stage=stage)
    return page(request,'requirements.html',active='requirements',title='需求管理',items=[{'req':r,'evidence':evidence(r)} for r in qs],stages=STAGES)
@login_required
def requirement_new(request):
    form=RequirementCreateForm(request.POST or None,request.FILES or None,projects=writable_projects(request.user),initial={'project':request.GET.get('project')})
    if request.method=='POST' and form.is_valid():
        try:
            req=create_requirement(request.user,form.cleaned_data); messages.success(request,'需求档案已建立；进展和确认材料分别保存。'); return redirect('requirement_detail',pk=req.pk)
        except ValidationError as e: error(form,e)
    return page(request,'form.html',active='requirements',title='登记新需求',subtitle='记录人员统一代录；提起人、负责人直接填写，无需建立后台账号。',form=form)
@login_required
def requirement_detail(request,pk):
    req=get_object_or_404(Requirement.objects.filter(project__in=projects_for(request.user)).select_related('project'),pk=pk)
    form=ProgressForm(request.POST or None,request.FILES or None,requirement=req)
    if request.method=='POST':
        require_recorder(request.user,req.project)
        if form.is_valid():
            try:
                record_progress(request.user,pk,form.cleaned_data); messages.success(request,'已追加进展与材料记录，历史内容保持只读。'); return redirect('requirement_detail',pk=pk)
            except ValidationError as e: error(form,e)
    return page(request,'requirement.html',active='requirements',title=req.title,req=req,evidence=evidence(req),form=form,writable=can_record(request.user,req.project),records=req.records.select_related('actor'),confirmations=req.confirmations.select_related('actor'),attachments=req.attachments.all(),bugs=req.bugs.all())
@login_required
def bug_list(request):
    qs=Bug.objects.filter(requirement__project__in=selected_projects(request)).select_related('requirement__project')
    q=request.GET.get('q',''); status=request.GET.get('status','')
    if q: qs=qs.filter(Q(title__icontains=q)|Q(number__icontains=q)|Q(business_owner__icontains=q))
    if status: qs=qs.filter(status=status)
    return page(request,'bugs.html',active='bugs',title='Bug 记录',items=qs,statuses=Bug.STATES)
@login_required
def bug_edit(request,pk=None,requirement_pk=None):
    bug=get_object_or_404(Bug.objects.filter(requirement__project__in=projects_for(request.user)),pk=pk) if pk else None
    requirements=Requirement.objects.filter(project__in=projects_for(request.user)).select_related('project')
    initial={}
    if requirement_pk: initial['requirement']=get_object_or_404(requirements,pk=requirement_pk)
    form=RecorderBugForm(request.POST or None,request.FILES or None,instance=bug,requirements=requirements,initial=initial)
    if request.method=='POST' and form.is_valid():
        try:
            obj=record_bug(request.user,form.cleaned_data,pk); messages.success(request,'Bug 结果已登记，旧记录继续保留。'); return redirect('bug_detail',pk=obj.pk)
        except ValidationError as e: error(form,e)
    return page(request,'form.html',active='bugs',title='查看 / 追加 Bug 记录' if bug else '登记 Bug',subtitle='不限需求当前环节；仅登记真实线下结果，不替开发或测试做审批。',form=form,readonly=bool(bug and not can_record(request.user,bug.requirement.project)),bug=bug,bug_events=bug.events.select_related('actor').prefetch_related('attachments') if bug else [],attachments=bug.requirement.attachments.filter(bug_event__bug=bug) if bug else [])

TITLES={'reviews':'版本与质量审查','data':'运营数据','anomalies':'数据波动复盘','assets':'资产与续费'}
FORMS={'reviews':ReviewForm,'data':DailyForm,'anomalies':AnomalyForm,'assets':AssetForm}
@login_required
def entity_list(request,kind):
    if kind not in ENTITY_MODELS: raise PermissionDenied
    qs=ENTITY_MODELS[kind].objects.filter(project__in=selected_projects(request)).select_related('project').order_by('-pk')
    totals={}; rows=qs
    if kind=='data':
        if request.GET.get('status') in dict(DailyData.STATES): rows=rows.filter(status=request.GET['status'])
        for param,lookup in [('start','day__gte'),('end','day__lte')]:
            try:
                if request.GET.get(param): rows=rows.filter(**{lookup:date.fromisoformat(request.GET[param])})
            except ValueError: pass
        if request.GET.get('channel'): rows=rows.filter(channel__icontains=request.GET['channel'])
        verified=rows.filter(status='verified'); totals=verified.aggregate(visits=Sum('visits'),cost=Sum('cost'))
        totals.update(missing_visits=verified.filter(visits__isnull=True).count(),missing_cost=verified.filter(cost__isnull=True).count(),verified=verified.count(),pending=rows.exclude(status='verified').count(),api=verified.filter(source_type='api').count(),manual=verified.filter(source_type='manual').count())
    return page(request,'entities.html',active=kind,title=TITLES[kind],kind=kind,items=rows,totals=totals)
@login_required
def entity_new(request,kind):
    if kind not in FORMS: raise PermissionDenied
    form=FORMS[kind](request.POST or None,projects=writable_projects(request.user),initial={'project':request.GET.get('project')})
    if request.method=='POST' and form.is_valid():
        try:
            obj=create_entity(request.user,kind,form.cleaned_data); messages.success(request,'记录已保存。'); return redirect('entity_detail',kind=kind,pk=obj.pk)
        except (ValidationError,IntegrityError) as e: error(form,e if isinstance(e,ValidationError) else ValidationError('该项目、渠道和统计日已有记录，请查看并更正，避免重复入账。'))
    return page(request,'form.html',active=kind,title={'reviews':'登记审查发现','data':'录入运营日报','anomalies':'登记新的波动事件','assets':'登记资产'}[kind],subtitle='业务人员无需登录；资料提供人、实际业务时间与录入人员分别记录。',form=form)
@login_required
def entity_detail(request,kind,pk):
    if kind not in ENTITY_MODELS: raise PermissionDenied
    obj=get_object_or_404(ENTITY_MODELS[kind].objects.filter(project__in=projects_for(request.user)).select_related('project'),pk=pk)
    before=state(obj)
    if kind=='data': form=DailyForm(request.POST or None,instance=obj,projects=projects_for(request.user))
    elif kind=='assets': form=RenewalForm(request.POST or None,obj=obj)
    else: form=UpdateForm(request.POST or None,kind=kind,obj=obj)
    if request.method=='POST':
        require_recorder(request.user,obj.project)
        if form.is_valid():
            try:
                update_entity(request.user,kind,pk,form.cleaned_data); messages.success(request,'已追加记录，原始登记和历史变更均已保留。'); return redirect('entity_detail',kind=kind,pk=pk)
            except ValidationError as e: error(form,e)
    fields=[(f.verbose_name, getattr(obj,'get_'+f.name+'_display')() if f.choices else before.get(f.name)) for f in obj._meta.fields if f.name not in ['id','project','created_at','updated_at']]
    return page(request,'form.html',active=kind,title={'reviews':'查看 / 更新审查记录','data':'查看 / 更正日报','anomalies':'更新波动核查记录','assets':'登记续费结果'}[kind],subtitle='仅追加处理历史，不覆盖原始登记。',form=form,readonly=not can_record(request.user,obj.project),summary_fields=fields,history=Journal.objects.filter(project=obj.project,entity_type=kind,entity_id=pk).select_related('actor'))

@login_required
def connections(request):
    form=ConnectionForm(request.POST or None,projects=writable_projects(request.user)); token=None
    if request.method=='POST' and form.is_valid():
        project=form.cleaned_data['project']; require_recorder(request.user,project)
        token=secrets.token_urlsafe(36)
        obj=DataConnection.objects.create(project=project,name=form.cleaned_data['name'],token_hash=hashlib.sha256(token.encode()).hexdigest())
        log(request.user,project,'创建数据接收接口',obj.name)
    ps=selected_projects(request)
    return page(request,'connections.html',active='data',title='运营数据 · 接口接入',form=form,token=token,items=DataConnection.objects.filter(project__in=ps),runs=SyncRun.objects.filter(connection__project__in=ps).select_related('connection')[:100])
@login_required
@require_POST
def connection_toggle(request,pk):
    with transaction.atomic():
        obj=get_object_or_404(DataConnection.objects.select_for_update(),pk=pk); require_recorder(request.user,obj.project)
        obj.enabled=not obj.enabled; obj.save(); log(request.user,obj.project,'调整接口启用状态',f'{obj.name}: {obj.enabled}')
    return redirect('connections')

@csrf_exempt
@require_POST
def receive_data(request):
    # Only this non-cookie API is CSRF-exempt; callers must present a scoped Bearer token.
    auth=request.headers.get('Authorization','')
    if not auth.startswith('Bearer '): return JsonResponse({'error':'需要 Bearer Token'},status=401)
    digest=hashlib.sha256(auth[7:].encode()).hexdigest()
    connection=DataConnection.objects.filter(token_hash=digest,enabled=True,project__archived=False).first()
    if not connection: return JsonResponse({'error':'接口凭据无效或已停用'},status=401)
    try:
        if len(request.body)>1024*1024: raise ValidationError('请求最大 1 MB。')
        payload=json.loads(request.body)
        if not isinstance(payload,dict): raise ValidationError('请求必须是 JSON 对象。')
        run=ingest(connection,payload)
        return JsonResponse({'request_id':run.request_id,'count':run.count,'conflicts':run.conflicts,'status':run.status})
    except (ValidationError,ValueError,TypeError) as e:
        SyncRun.objects.get_or_create(connection=connection,request_id='error-'+secrets.token_hex(12),defaults={'payload_hash':'','status':'error','message':str(e)[:1000]})
        return JsonResponse({'error':str(e)},status=400)
    except PermissionDenied: return JsonResponse({'error':'接口已停用或项目已归档'},status=403)

@login_required
def reports(request):
    from .reports import make_snapshot
    form=ReportForm(request.POST or None,projects=writable_projects(request.user),initial={'start':timezone.localdate()-timedelta(days=6),'end':timezone.localdate()})
    if request.method=='POST' and form.is_valid():
        data=form.cleaned_data; ps=writable_projects(request.user)
        if data.get('project'): ps=ps.filter(pk=data['project'].pk)
        if data['kind']=='requirement': ps=ps.filter(pk=data['requirement'].project_id)
        if not ps.exists(): raise PermissionDenied('没有可生成报告的项目。')
        for project in ps: require_recorder(request.user,project)
        report=Report.objects.create(project=data.get('project'),actor=request.user,kind=data['kind'],start=data['start'],end=data['end'],snapshot=make_snapshot(ps,data))
        for project in ps: log(request.user,project,'生成报告预览',f'REP-{report.pk}')
        return redirect('report_detail',pk=report.pk)
    items=Report.objects.all() if request.user.is_superuser else Report.objects.filter(actor=request.user)
    return page(request,'reports.html',active='reports',title='报告中心',form=form,items=items)

def report_access(request,pk):
    report=get_object_or_404(Report,pk=pk)
    if not request.user.is_superuser and report.actor_id!=request.user.pk: raise PermissionDenied
    ids=set(report.snapshot.get('project_ids',[]))
    if not ids.issubset(set(projects_for(request.user).values_list('pk',flat=True))): raise PermissionDenied
    return report
@login_required
def report_detail(request,pk):
    report=report_access(request,pk)
    return page(request,'report.html',active='reports',title='产品与运营工作报告',report=report,snapshot=report.snapshot)
@login_required
@require_POST
def report_archive(request,pk):
    with transaction.atomic():
        report=report_access(request,pk)
        for project in Project.objects.filter(pk__in=report.snapshot['project_ids']): require_recorder(request.user,project)
        if not report.archived_at:
            Report.objects.filter(pk=pk,archived_at=None).update(archived_at=timezone.now())
            for project in Project.objects.filter(pk__in=report.snapshot['project_ids']): log(request.user,project,'归档报告',f'REP-{pk}')
    return redirect('report_detail',pk=pk)
@login_required
def report_download(request,pk,format):
    from .reports import export_pdf, export_csv, export_materials, export_xlsx
    report=report_access(request,pk)
    if format=='pdf': content=export_pdf(report); mime='application/pdf'; suffix='pdf'
    elif format=='csv': content=export_csv(report); mime='text/csv; charset=utf-8'; suffix='csv'
    elif format=='xlsx': content=export_xlsx(report); mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'; suffix='xlsx'
    elif format=='zip': content=export_materials(report); mime='application/zip'; suffix='zip'
    else: raise PermissionDenied
    response=HttpResponse(content,content_type=mime); response['Content-Disposition']=f'attachment; filename="report-{pk}.{suffix}"'
    for project in Project.objects.filter(pk__in=report.snapshot['project_ids']): log(request.user,project,'导出报告',f'REP-{pk} · {format}')
    return response
