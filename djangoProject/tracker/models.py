import uuid
from django.conf import settings
from django.db import models
from django.core.validators import MinValueValidator
from django.utils import timezone

STAGES = [(str(i), s) for i, s in enumerate(['需求提出','需求规范整理','需求确认','分配开发','开发接单与实现','测试验收','需求方验收','代码上传','代码审核','结项检查','已完成'])]
PRIORITIES = [('P0','P0 · 紧急'),('P1','P1 · 高'),('P2','P2 · 中'),('P3','P3 · 低')]
ROLES = [(s,s) for s in ['需求提起人','产品负责人','分配负责人','开发人员','测试负责人','代码审核人','结项人','记录人员']]
def req_number(): return 'REQ-' + uuid.uuid4().hex[:12].upper()
def bug_number(): return 'BUG-' + uuid.uuid4().hex[:12].upper()

class Project(models.Model):
    name = models.CharField('项目名称', max_length=120)
    description = models.TextField('项目说明', blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, verbose_name='项目负责人')
    archived = models.BooleanField('已归档', default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    def __str__(self): return self.name
    @property
    def progress(self):
        total = self.requirements.count()
        return round(self.requirements.filter(stage='10').count() / total * 100) if total else 0
    @property
    def overdue_count(self): return self.requirements.exclude(stage='10').filter(due_date__lt=timezone.localdate()).count()
    class Meta: verbose_name = '项目'; verbose_name_plural = '项目'

class Membership(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='memberships')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, verbose_name='成员')
    role = models.CharField('角色', max_length=30, choices=ROLES)
    class Meta:
        unique_together = ('project','user','role')
        verbose_name = '项目成员'; verbose_name_plural = '项目成员'

class Requirement(models.Model):
    number = models.CharField(max_length=20, unique=True, default=req_number, editable=False)
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name='requirements', verbose_name='项目')
    business_owner = models.CharField('当前业务负责人', max_length=120, blank=True)
    proposer = models.CharField('需求提起人', max_length=120, blank=True)
    version = models.CharField('需求版本', max_length=100, blank=True)
    source_group = models.CharField('来源群组 / 标识', max_length=200, blank=True)
    title = models.CharField('需求标题', max_length=200)
    body = models.TextField('需求正文 / 验收标准')
    priority = models.CharField('优先级', max_length=2, choices=PRIORITIES, default='P2')
    stage = models.CharField('当前环节', max_length=2, choices=STAGES, default='0')
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='assigned_requirements', verbose_name='原账号负责人', null=True, blank=True)
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='created_requirements')
    round = models.PositiveIntegerField('处理轮次', default=1)
    due_date = models.DateField('截止日期', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    @property
    def overdue(self): return self.stage != '10' and self.due_date and self.due_date < timezone.localdate()
    def __str__(self): return f'{self.number} {self.title}'
    class Meta: ordering=['-updated_at']; verbose_name='需求'; verbose_name_plural='需求'

class Record(models.Model):
    requirement = models.ForeignKey(Requirement, on_delete=models.PROTECT, related_name='records')
    stage = models.CharField(max_length=2, choices=STAGES)
    round = models.PositiveIntegerField()
    action = models.CharField(max_length=30)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+', null=True, blank=True)
    receiver = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    note = models.TextField()
    commit = models.CharField(max_length=200, blank=True)
    version = models.CharField(max_length=100, blank=True)
    snapshot = models.JSONField(default=dict)
    payload = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField('业务发生时间', null=True, blank=True)
    incomplete = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: ordering=['-created_at']; verbose_name='流程记录'; verbose_name_plural='流程记录'

class Bug(models.Model):
    STATES=[('open','待修复'),('verify','待回归'),('closed','已关闭')]
    number=models.CharField(max_length=20, unique=True, default=bug_number, editable=False)
    requirement=models.ForeignKey(Requirement,on_delete=models.PROTECT,related_name='bugs')
    test_record=models.ForeignKey(Record,on_delete=models.PROTECT,null=True,blank=True)
    business_owner=models.CharField('开发负责人',max_length=120,blank=True)
    discovery_stage=models.CharField('发现环节',max_length=2,choices=STAGES,default='5')
    round=models.PositiveIntegerField()
    title=models.CharField('Bug 标题',max_length=200)
    steps=models.TextField('复现步骤')
    actual=models.TextField('实际结果')
    expected=models.TextField('期望结果')
    environment=models.CharField('运行环境',max_length=200)
    discovered_version=models.CharField('发现版本',max_length=100)
    severity=models.CharField('严重程度',max_length=10,choices=[('critical','致命'),('major','严重'),('normal','一般'),('minor','轻微')],default='normal')
    priority=models.CharField('优先级',max_length=2,choices=PRIORITIES,default='P2')
    owner=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,verbose_name='原账号负责人',null=True,blank=True)
    status=models.CharField(max_length=10,choices=STATES,default='open')
    fix_version=models.CharField(max_length=100,blank=True)
    created_at=models.DateTimeField(auto_now_add=True)
    updated_at=models.DateTimeField(auto_now=True)
    class Meta: ordering=['-updated_at']; verbose_name='Bug'; verbose_name_plural='Bug'
    def __str__(self): return f'{self.number} {self.title}'

class BugEvent(models.Model):
    bug=models.ForeignKey(Bug,on_delete=models.PROTECT,related_name='events')
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    action=models.CharField(max_length=30)
    payload=models.JSONField(default=dict,blank=True)
    occurred_at=models.DateTimeField(null=True,blank=True)
    note=models.TextField()
    commit=models.CharField(max_length=200,blank=True)
    version=models.CharField(max_length=100,blank=True)
    previous_owner=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='+', null=True, blank=True)
    receiver=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='+', null=True, blank=True)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta: ordering=['-created_at']

class Attachment(models.Model):
    requirement=models.ForeignKey(Requirement,on_delete=models.PROTECT,related_name='attachments')
    record=models.ForeignKey(Record,on_delete=models.PROTECT,null=True,blank=True)
    bug_event=models.ForeignKey(BugEvent,on_delete=models.PROTECT,null=True,blank=True,related_name='attachments')
    kind=models.CharField(max_length=20,default='document',choices=[('screenshot','群聊截图'),('document','文档附件')])
    version=models.CharField(max_length=100,blank=True)
    confirmation=models.ForeignKey('Confirmation',on_delete=models.PROTECT,null=True,blank=True,related_name='attachments')
    stage=models.CharField(max_length=2,choices=STAGES)
    round=models.PositiveIntegerField()
    file=models.FileField(upload_to='attachments/%Y/%m/')
    name=models.CharField(max_length=255)
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    created_at=models.DateTimeField(auto_now_add=True)

class Audit(models.Model):
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    project=models.ForeignKey(Project,on_delete=models.PROTECT,null=True)
    action=models.CharField(max_length=100)
    detail=models.TextField()
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta: ordering=['-created_at']; verbose_name='操作日志'; verbose_name_plural='操作日志'

class Notification(models.Model):
    user=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE)
    text=models.CharField(max_length=255)
    url=models.CharField(max_length=200)
    read=models.BooleanField(default=False)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta: ordering=['-created_at']


class Confirmation(models.Model):
    requirement = models.ForeignKey(Requirement, on_delete=models.PROTECT, related_name='confirmations')
    stage = models.CharField('确认环节', max_length=2, choices=STAGES[:10])
    round = models.PositiveIntegerField(default=1)
    version = models.CharField('对应版本', max_length=100, blank=True)
    confirmer = models.CharField('群组确认人', max_length=120, blank=True)
    confirmed_at = models.DateTimeField('群组确认时间', null=True, blank=True)
    conclusion = models.CharField('聊天确认结论', max_length=20, choices=[('pending','待明确'),('confirmed','明确通过'),('rejected','未通过')], default='pending')
    summary = models.TextField('确认原意摘要', blank=True)
    checked = models.BooleanField('已核对截图中的确认人、版本和明确意见', default=False)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: ordering = ['-created_at', '-pk']

class Review(models.Model):
    project=models.ForeignKey(Project,on_delete=models.PROTECT)
    title=models.CharField('问题标题',max_length=200)
    version=models.CharField('对应版本',max_length=100)
    provider=models.CharField('意见提供人',max_length=120)
    category=models.CharField('审查类别',max_length=20,choices=[('frontend','前端审查'),('backend','后端审查'),('test','测试复查')])
    risk=models.CharField('建议风险等级',max_length=10,choices=[('low','低'),('medium','中'),('high','高')],default='medium')
    description=models.TextField('现象、证据与优化建议')
    reproduction=models.TextField('人工复现资料 / 说明',blank=True)
    status=models.CharField('线下处理情况',max_length=20,choices=[('pending','待决策'),('working','处理中'),('done','已处理'),('deferred','暂缓')],default='pending')
    created_at=models.DateTimeField(auto_now_add=True)
    updated_at=models.DateTimeField(auto_now=True)
    def __str__(self): return self.title

class DailyData(models.Model):
    STATES=[('pending','待核对'),('verified','已核对'),('conflict','待处理差异')]
    project=models.ForeignKey(Project,on_delete=models.PROTECT)
    day=models.DateField('统计日期')
    channel=models.CharField('渠道',max_length=100)
    provider=models.CharField('资料提供人',max_length=120)
    visits=models.PositiveIntegerField('访问次数（不详留空）',null=True,blank=True)
    registrations=models.PositiveIntegerField('注册数（不详留空）',null=True,blank=True)
    cost=models.DecimalField('推广费用 USD',max_digits=16,decimal_places=2,null=True,blank=True,validators=[MinValueValidator(0)])
    revenue=models.DecimalField('收入 USD（未归因）',max_digits=16,decimal_places=2,null=True,blank=True,validators=[MinValueValidator(0)])
    source=models.TextField('来源文件 / 说明')
    source_type=models.CharField(max_length=10,choices=[('manual','人工录入'),('api','接口同步')],default='manual')
    note=models.TextField('备注',blank=True)
    status=models.CharField(max_length=20,choices=STATES,default='pending')
    updated_at=models.DateTimeField(auto_now=True)
    class Meta:
        ordering=['-day','-pk']
        constraints=[models.UniqueConstraint(fields=['project','day','channel'],name='unique_project_channel_day')]

class Anomaly(models.Model):
    project=models.ForeignKey(Project,on_delete=models.PROTECT)
    title=models.CharField('波动标题',max_length=200)
    metric=models.CharField('统计指标及单位',max_length=100)
    before_at=models.DateTimeField('前时点（可留空）',null=True,blank=True)
    after_at=models.DateTimeField('后时点（可留空）',null=True,blank=True)
    before_value=models.DecimalField('前时点数值',max_digits=18,decimal_places=4,null=True,blank=True)
    after_value=models.DecimalField('后时点数值',max_digits=18,decimal_places=4,null=True,blank=True)
    provider=models.CharField('资料提供人',max_length=120)
    source=models.TextField('数据来源 / 材料说明')
    description=models.TextField('初步现象与待核查事项')
    status=models.CharField('核查状态',max_length=20,choices=[('pending','待核查'),('working','核查中'),('done','已留存结论')],default='pending')
    created_at=models.DateTimeField(auto_now_add=True)
    updated_at=models.DateTimeField(auto_now=True)
    @property
    def change_percent(self):
        if self.before_value in (None,0) or self.after_value is None: return None
        return round((self.after_value-self.before_value)/abs(self.before_value)*100,1)

class Asset(models.Model):
    project=models.ForeignKey(Project,on_delete=models.PROTECT)
    name=models.CharField('资产名称',max_length=200)
    kind=models.CharField('类型',max_length=20,choices=[('domain','域名'),('server','服务器'),('account','平台账号')])
    identifier=models.CharField('资源 / 账号标识',max_length=200,blank=True)
    supplier=models.CharField('供应商 / 所属账号',max_length=200,blank=True)
    business_owner=models.CharField('业务负责人',max_length=120)
    expires_on=models.DateField('到期日')
    cost=models.DecimalField('费用 USD',max_digits=16,decimal_places=2,default=0,validators=[MinValueValidator(0)])
    purpose=models.TextField('用途')
    created_at=models.DateTimeField(auto_now_add=True)
    updated_at=models.DateTimeField(auto_now=True)
    @property
    def remaining_days(self): return (self.expires_on-timezone.localdate()).days

class Journal(models.Model):
    """Immutable business event; snapshots preserve values before and after each change."""
    project=models.ForeignKey(Project,on_delete=models.PROTECT)
    entity_type=models.CharField(max_length=30)
    entity_id=models.PositiveBigIntegerField()
    action=models.CharField(max_length=80)
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    payload=models.JSONField(default=dict)
    snapshot=models.JSONField(default=dict)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta: ordering=['-created_at','-pk']

class DataConnection(models.Model):
    project=models.ForeignKey(Project,on_delete=models.PROTECT)
    name=models.CharField('接口名称',max_length=120)
    token_hash=models.CharField(max_length=64,unique=True)
    enabled=models.BooleanField('启用',default=True)
    created_at=models.DateTimeField(auto_now_add=True)

class SyncRun(models.Model):
    connection=models.ForeignKey(DataConnection,on_delete=models.PROTECT)
    request_id=models.CharField(max_length=100)
    payload_hash=models.CharField(max_length=64)
    count=models.PositiveIntegerField(default=0)
    conflicts=models.PositiveIntegerField(default=0)
    status=models.CharField(max_length=20,default='success')
    message=models.TextField(blank=True)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering=['-created_at']
        constraints=[models.UniqueConstraint(fields=['connection','request_id'],name='unique_sync_request')]

class Report(models.Model):
    project=models.ForeignKey(Project,on_delete=models.PROTECT,null=True,blank=True)
    actor=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    kind=models.CharField(max_length=30)
    start=models.DateField()
    end=models.DateField()
    snapshot=models.JSONField(default=dict)
    archived_at=models.DateTimeField(null=True,blank=True)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta: ordering=['-created_at']
