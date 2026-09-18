from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q
from .models import Project, Requirement, Bug, STAGES, PRIORITIES
from .permissions import requirement_actions, bug_actions
User=get_user_model()
def members(project):
    return User.objects.filter(is_active=True).filter(Q(membership__project=project)|Q(pk=project.owner_id)).distinct()
class ProjectForm(forms.ModelForm):
    members=forms.ModelMultipleChoiceField(label='项目成员',queryset=User.objects.filter(is_active=True),required=False,widget=forms.CheckboxSelectMultiple)
    class Meta: model=Project; fields=['name','description','owner','members']
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs); self.fields['owner'].queryset=User.objects.filter(is_active=True)
class RequirementForm(forms.ModelForm):
    class Meta:
        model=Requirement; fields=['project','title','body','priority','owner','due_date']
        widgets={'due_date':forms.DateInput(attrs={'type':'date'}),'body':forms.Textarea(attrs={'rows':7})}
    def __init__(self,*args,projects=None,project=None,**kwargs):
        super().__init__(*args,**kwargs)
        if projects is not None: self.fields['project'].queryset=projects.filter(archived=False)
        self.fields['owner'].queryset=members(project) if project else User.objects.filter(is_active=True,pk__in=[])
class ProcessForm(forms.Form):
    revision=forms.IntegerField(widget=forms.HiddenInput)
    action=forms.ChoiceField(label='处理方式',choices=[('advance','确认并进入下一环节'),('return','退回上游环节'),('transfer','转交负责人'),('note','补充记录')])
    target=forms.ChoiceField(label='退回到',choices=STAGES,required=False)
    receiver=forms.ModelChoiceField(label='下一负责人 / 接收人',queryset=User.objects.none(),required=False)
    note=forms.CharField(label='处理结论 / 原因',widget=forms.Textarea(attrs={'rows':4}))
    body=forms.CharField(label='更新需求正文（仅退回或补充时填写）',widget=forms.Textarea(attrs={'rows':3}),required=False)
    title=forms.CharField(label='更新需求标题（可选）',max_length=200,required=False)
    priority=forms.ChoiceField(label='调整优先级（可选）',choices=[('', '保持不变')]+PRIORITIES,required=False)
    due_date=forms.DateField(label='调整截止日期（可选）',required=False,widget=forms.DateInput(attrs={'type':'date'}))
    commit=forms.CharField(label='代码提交编号',required=False,max_length=200)
    version=forms.CharField(label='代码 / 测试版本',required=False,max_length=100)
    risk_reason=forms.CharField(label='存在未关闭 Bug 时仍通过的原因',required=False,widget=forms.Textarea(attrs={'rows':2}))
    file=forms.FileField(label='截图或附件（可选）',required=False)
    def __init__(self,*args,requirement,user,**kwargs):
        super().__init__(*args,**kwargs); self.fields['receiver'].queryset=members(requirement.project)
        self.fields['target'].choices=[('', '选择上游环节')]+STAGES[:int(requirement.stage)]
        self.fields['revision'].initial=requirement.records.count()
        self.fields['action'].choices=requirement_actions(user,requirement)
        if requirement.stage=='10':
            for name in ['target','receiver','body','title','priority','due_date','commit','version','risk_reason']:
                self.fields.pop(name)
            self.fields['action'].initial='note'
class BugForm(forms.ModelForm):
    file=forms.FileField(label='截图或附件',required=False)
    class Meta: model=Bug; fields=['title','steps','actual','expected','environment','discovered_version','severity','priority','owner']
    def __init__(self,*args,project,**kwargs):
        super().__init__(*args,**kwargs); self.fields['owner'].queryset=members(project)
        for name in ['steps','actual','expected']: self.fields[name].widget.attrs['rows']=3
class BugActionForm(forms.Form):
    revision=forms.IntegerField(widget=forms.HiddenInput)
    action=forms.ChoiceField(label='处理方式',choices=[('fix','提交修复'),('pass','回归通过并关闭'),('fail','回归失败 / 重新打开'),('assign','指派负责人')])
    note=forms.CharField(label='修复说明 / 回归结果 / 指派原因',widget=forms.Textarea(attrs={'rows':4}))
    commit=forms.CharField(label='代码提交编号（修复必填）',max_length=200,required=False)
    version=forms.CharField(label='待验证 / 回归测试版本',max_length=100,required=False)
    receiver=forms.ModelChoiceField(label='接收人',queryset=User.objects.none(),required=False)
    file=forms.FileField(label='回归截图或附件',required=False)
    def __init__(self,*args,bug,user,**kwargs):
        super().__init__(*args,**kwargs); self.fields['receiver'].queryset=members(bug.requirement.project); self.fields['revision'].initial=bug.events.count()

        self.fields['action'].choices=bug_actions(user,bug)
        if self.fields['action'].choices:
            self.fields['action'].initial=self.fields['action'].choices[0][0]
        if bug.status=='closed':
            self.fields.pop('commit')
            self.fields['note'].label='重新打开原因'
            self.fields['version'].label='复测版本'
