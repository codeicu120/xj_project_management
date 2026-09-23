from django import forms
from django.core.exceptions import ValidationError
from .models import *

class MultiInput(forms.ClearableFileInput):
    allow_multiple_selected=True
class MultiFiles(forms.FileField):
    def clean(self,data,initial=None):
        if not data: return []
        return [super(MultiFiles,self).clean(item,initial) for item in (data if isinstance(data,(list,tuple)) else [data])]
class MaterialsForm(forms.Form):
    screenshots=MultiFiles(label='群组确认截图（PNG / JPG / WebP，每张最多 5 MB）',required=False,widget=MultiInput)
    documents=MultiFiles(label='文档 / 其他附件（PDF / Word / Excel / TXT / ZIP，每份最多 20 MB）',required=False,widget=MultiInput)
    def clean_screenshots(self):
        from .recorder_services import validate_materials
        files=self.cleaned_data['screenshots']; validate_materials(files,[]); return files
    def clean_documents(self):
        from .recorder_services import validate_materials
        files=self.cleaned_data['documents']; validate_materials([],files); return files

class EvidenceForm(MaterialsForm):
    confirmer=forms.CharField(label='群组确认人',max_length=120,required=False)
    confirmed_at=forms.DateTimeField(label='群组确认时间（不详可留空）',required=False,widget=forms.DateTimeInput(attrs={'type':'datetime-local'}))
    conclusion=forms.ChoiceField(label='聊天确认结论',choices=Confirmation._meta.get_field('conclusion').choices,initial='pending')
    summary=forms.CharField(label='确认原意摘要',required=False,widget=forms.Textarea(attrs={'rows':3}))
    checked=forms.BooleanField(label='已核对截图中的确认人、对应版本和明确意见',required=False)
    def clean(self):
        data=super().clean()
        if data.get('checked') and (not data.get('confirmer') or not data.get('summary') or data.get('conclusion')=='pending'):
            raise ValidationError('核对完成需填写确认人、明确结论和原意摘要；资料可先保存，后续补充核对。')
        return data

class RequirementCreateForm(EvidenceForm,forms.ModelForm):
    class Meta:
        model=Requirement
        fields=['title','project','priority','proposer','business_owner','due_date','version','source_group','body']
        widgets={'due_date':forms.DateInput(attrs={'type':'date'}),'body':forms.Textarea(attrs={'rows':4})}
    def __init__(self,*args,projects,**kwargs):
        super().__init__(*args,**kwargs); self.fields['project'].queryset=projects.filter(archived=False)
        for name in ['proposer','business_owner']: self.fields[name].required=True
        self.fields['body'].required=False

class ProgressForm(EvidenceForm):
    revision=forms.IntegerField(widget=forms.HiddenInput)
    stage=forms.ChoiceField(label='记录环节',choices=STAGES)
    business_owner=forms.CharField(label='本次业务负责人',max_length=120)
    version=forms.CharField(label='对应版本',max_length=100,required=False)
    occurred_at=forms.DateTimeField(label='业务发生时间（不详可留空）',required=False,widget=forms.DateTimeInput(attrs={'type':'datetime-local'}))
    note=forms.CharField(label='本次具体进展',widget=forms.Textarea(attrs={'rows':3}))
    completed=forms.CharField(label='已完成内容',required=False,widget=forms.Textarea(attrs={'rows':2}))
    blockers=forms.CharField(label='待完成内容 / 阻塞',required=False,widget=forms.Textarea(attrs={'rows':2}))
    next_step=forms.CharField(label='下一步安排',required=False)
    expected_on=forms.DateField(label='预计完成时间',required=False,widget=forms.DateInput(attrs={'type':'date'}))
    source=forms.CharField(label='反馈来源 / 群组说明',required=False,max_length=200)
    save_mode=forms.ChoiceField(label='保存方式',choices=[('append','仅追加进展，不改变当前环节'),('update','同时更新当前环节、负责人及版本')])
    round_mode=forms.ChoiceField(label='处理轮次',choices=[('keep','保留当前轮次'),('new','返工，开启新轮次')])
    evidence_stage=forms.ChoiceField(label='补充确认材料所属环节',choices=[('','本次记录环节')]+STAGES[:10],required=False)
    evidence_round=forms.IntegerField(label='补充材料所属轮次（留空使用本次轮次）',min_value=1,required=False)
    def __init__(self,*args,requirement,**kwargs):
        super().__init__(*args,**kwargs); self.fields['revision'].initial=requirement.records.count()
        self.fields['stage'].initial=requirement.stage; self.fields['business_owner'].initial=requirement.business_owner; self.fields['version'].initial=requirement.version
        self.fields['round_mode'].initial='keep'; self.fields['save_mode'].initial='append'
        self.order_fields(['revision','stage','business_owner','version','occurred_at','note','completed','blockers','next_step','expected_on','source','save_mode','round_mode','evidence_stage','evidence_round','confirmer','confirmed_at','conclusion','summary','screenshots','documents','checked'])
    def clean(self):
        data=super().clean()
        if data.get('round_mode')=='new' and data.get('save_mode')!='update': raise ValidationError('开启返工轮次时请选择同时更新当前环节。')
        return data

class RecorderBugForm(MaterialsForm,forms.ModelForm):
    revision=forms.IntegerField(widget=forms.HiddenInput,required=False)
    provider=forms.CharField(label='结果提供人',max_length=120)
    occurred_at=forms.DateTimeField(label='实际处理时间（不详可留空）',required=False,widget=forms.DateTimeInput(attrs={'type':'datetime-local'}))
    note=forms.CharField(label='问题说明 / 修复或回归结果',widget=forms.Textarea(attrs={'rows':4}))
    source=forms.CharField(label='群组依据 / 材料说明',max_length=200,required=False)
    class Meta:
        model=Bug; fields=['requirement','title','discovery_stage','status','business_owner','discovered_version']
    def __init__(self,*args,requirements,**kwargs):
        super().__init__(*args,**kwargs); self.fields['requirement'].queryset=requirements.filter(project__archived=False)
        self.fields['business_owner'].required=True; self.fields['discovered_version'].required=False; self.fields['discovered_version'].label='发现 / 修复版本'
        if self.instance.pk:
            self.fields['requirement'].disabled=True; self.fields['revision'].required=True; self.fields['revision'].initial=self.instance.events.count()

class ProjectModelForm(forms.ModelForm):
    def __init__(self,*args,projects,**kwargs):
        super().__init__(*args,**kwargs)
        if 'project' in self.fields: self.fields['project'].queryset=projects.filter(archived=False)
        for name,field in self.fields.items():
            if isinstance(field,forms.DateTimeField): field.widget=forms.DateTimeInput(attrs={'type':'datetime-local'})
            elif isinstance(field,forms.DateField): field.widget=forms.DateInput(attrs={'type':'date'})
            if isinstance(field.widget,forms.Textarea): field.widget.attrs['rows']=3
            if name in ['cost','revenue','visits','registrations']: field.min_value=0; field.widget.attrs['min']=0
    def clean(self):
        data=super().clean()
        for name in ['cost','revenue','visits','registrations']:
            if data.get(name) is not None and data[name]<0: self.add_error(name,'数值不能为负数。')
        return data
class ReviewForm(ProjectModelForm):
    class Meta: model=Review; fields=['title','project','version','provider','category','risk','description','reproduction']
class DailyForm(ProjectModelForm):
    verified=forms.BooleanField(label='已对照原始资料核对数值（不代表线上数据已验证）',required=False)
    revision=forms.IntegerField(widget=forms.HiddenInput,required=False)
    reason=forms.CharField(label='更正原因（修改数据时必填）',required=False,widget=forms.Textarea(attrs={'rows':2}))
    class Meta: model=DailyData; fields=['day','project','channel','provider','visits','registrations','cost','revenue','source','note']
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        if self.instance.pk:
            for key in ['project','day','channel']: self.fields[key].disabled=True
            self.fields['revision'].required=True; self.fields['revision'].initial=Journal.objects.filter(entity_type='data',entity_id=self.instance.pk).count()
            self.fields['verified'].initial=self.instance.status=='verified'
class AnomalyForm(ProjectModelForm):
    class Meta: model=Anomaly; fields=['title','project','metric','before_at','after_at','before_value','after_value','provider','source','description']
    def clean(self):
        data=super().clean()
        if data.get('before_at') and data.get('after_at') and data['before_at']>data['after_at']: raise ValidationError('后时点不能早于前时点。')
        return data
class AssetForm(ProjectModelForm):
    class Meta: model=Asset; fields=['name','kind','project','identifier','supplier','business_owner','expires_on','cost','purpose']

class UpdateForm(forms.Form):
    revision=forms.IntegerField(widget=forms.HiddenInput)
    provider=forms.CharField(label='本次意见 / 结果提供人',max_length=120)
    status=forms.ChoiceField(label='更新后状态',choices=[])
    occurred_at=forms.DateTimeField(label='实际处理时间（可留空）',required=False,widget=forms.DateTimeInput(attrs={'type':'datetime-local'}))
    source=forms.CharField(label='反馈来源 / 材料说明',max_length=500)
    note=forms.CharField(label='本次意见与后续建议',widget=forms.Textarea(attrs={'rows':4}))
    def __init__(self,*args,kind,obj,**kwargs):
        super().__init__(*args,**kwargs); self.fields['status'].choices=obj._meta.get_field('status').choices; self.fields['status'].initial=obj.status
        self.fields['revision'].initial=Journal.objects.filter(entity_type=kind,entity_id=obj.pk).count()
class RenewalForm(forms.Form):
    revision=forms.IntegerField(widget=forms.HiddenInput)
    provider=forms.CharField(label='实际办理人',max_length=120)
    handled_on=forms.DateField(label='办理日期',widget=forms.DateInput(attrs={'type':'date'}))
    expires_on=forms.DateField(label='新到期日',widget=forms.DateInput(attrs={'type':'date'}))
    cost=forms.DecimalField(label='续费金额 USD',max_digits=16,decimal_places=2,min_value=0)
    source=forms.CharField(label='付款 / 续费凭证说明',max_length=500)
    note=forms.CharField(label='用途或续费备注',required=False,widget=forms.Textarea(attrs={'rows':3}))
    verified=forms.BooleanField(label='已根据办理结果核实新有效期；仅计划续费不应更新到期日',required=True)
    def __init__(self,*args,obj,**kwargs):
        super().__init__(*args,**kwargs); self.fields['revision'].initial=Journal.objects.filter(entity_type='assets',entity_id=obj.pk).count()
class ConnectionForm(ProjectModelForm):
    class Meta: model=DataConnection; fields=['project','name']
class ReportForm(forms.Form):
    kind=forms.ChoiceField(label='报告类型',choices=[('weekly','项目周报'),('requirement','单需求档案'),('operations','运营工作报告')])
    project=forms.ModelChoiceField(label='所属项目（留空为全部有权项目）',queryset=Project.objects.none(),required=False)
    start=forms.DateField(label='开始日期',widget=forms.DateInput(attrs={'type':'date'}))
    end=forms.DateField(label='结束日期',widget=forms.DateInput(attrs={'type':'date'}))
    requirement=forms.ModelChoiceField(label='单需求档案对应需求',queryset=Requirement.objects.none(),required=False)
    include_materials=forms.BooleanField(label='包含截图附录及待补清单',required=False,initial=True)
    def __init__(self,*args,projects,**kwargs):
        super().__init__(*args,**kwargs); self.fields['project'].queryset=projects; self.fields['requirement'].queryset=Requirement.objects.filter(project__in=projects)
    def clean(self):
        data=super().clean()
        if data.get('start') and data.get('end') and data['start']>data['end']: raise ValidationError('结束日期不能早于开始日期。')
        if data.get('kind')=='requirement' and not data.get('requirement'): self.add_error('requirement','请选择对应需求。')
        if data.get('project') and data.get('requirement') and data['requirement'].project_id!=data['project'].pk: self.add_error('requirement','需求不属于所选项目。')
        return data
