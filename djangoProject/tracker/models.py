import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone

STAGES = [(str(i), s) for i, s in enumerate(['需求提出','需求规范整理','需求确认','分配开发','开发接单与实现','测试验收','需求方验收','代码上传','代码审核','结项检查','已完成'])]
PRIORITIES = [('P0','P0 · 紧急'),('P1','P1 · 高'),('P2','P2 · 中'),('P3','P3 · 低')]
ROLES = [(s,s) for s in ['需求提起人','产品负责人','分配负责人','开发人员','测试负责人','代码审核人','结项人']]
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
    title = models.CharField('需求标题', max_length=200)
    body = models.TextField('需求正文 / 验收标准')
    priority = models.CharField('优先级', max_length=2, choices=PRIORITIES, default='P2')
    stage = models.CharField('当前环节', max_length=2, choices=STAGES, default='0')
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='assigned_requirements', verbose_name='当前负责人')
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
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    receiver = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    note = models.TextField()
    commit = models.CharField(max_length=200, blank=True)
    version = models.CharField(max_length=100, blank=True)
    snapshot = models.JSONField(default=dict)
    incomplete = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: ordering=['-created_at']; verbose_name='流程记录'; verbose_name_plural='流程记录'

class Bug(models.Model):
    STATES=[('open','待修复'),('verify','待回归'),('closed','已关闭')]
    number=models.CharField(max_length=20, unique=True, default=bug_number, editable=False)
    requirement=models.ForeignKey(Requirement,on_delete=models.PROTECT,related_name='bugs')
    test_record=models.ForeignKey(Record,on_delete=models.PROTECT)
    round=models.PositiveIntegerField()
    title=models.CharField('Bug 标题',max_length=200)
    steps=models.TextField('复现步骤')
    actual=models.TextField('实际结果')
    expected=models.TextField('期望结果')
    environment=models.CharField('运行环境',max_length=200)
    discovered_version=models.CharField('发现版本',max_length=100)
    severity=models.CharField('严重程度',max_length=10,choices=[('critical','致命'),('major','严重'),('normal','一般'),('minor','轻微')],default='normal')
    priority=models.CharField('优先级',max_length=2,choices=PRIORITIES,default='P2')
    owner=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,verbose_name='当前负责人')
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
    note=models.TextField()
    commit=models.CharField(max_length=200,blank=True)
    version=models.CharField(max_length=100,blank=True)
    previous_owner=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='+')
    receiver=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='+')
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta: ordering=['-created_at']

class Attachment(models.Model):
    requirement=models.ForeignKey(Requirement,on_delete=models.PROTECT,related_name='attachments')
    record=models.ForeignKey(Record,on_delete=models.PROTECT,null=True,blank=True)
    bug_event=models.ForeignKey(BugEvent,on_delete=models.PROTECT,null=True,blank=True,related_name='attachments')
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
