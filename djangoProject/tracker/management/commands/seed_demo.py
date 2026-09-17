import secrets
from datetime import timedelta
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from tracker.models import *
from tracker.services import snapshot
class Command(BaseCommand):
    help='创建本地演示账号与项目。仅首次创建账号时设置密码，不覆盖现有数据。'
    def add_arguments(self,parser): parser.add_argument('--password',default=None)
    @transaction.atomic
    def handle(self,*args,**options):
        User=get_user_model()
        if User.objects.filter(username='demo').exists():
            self.stdout.write('演示账号已存在，不重复写入。'); return
        password=options['password'] or secrets.token_urlsafe(15)
        user=User.objects.create_superuser('demo',password=password,first_name='林',last_name='知行')
        developer=User.objects.create_user('developer',first_name='陈',last_name='远'); developer.set_unusable_password(); developer.save()
        tester=User.objects.create_user('tester',first_name='苏',last_name='晓'); tester.set_unusable_password(); tester.save()
        for index,(name,desc) in enumerate([('星际项目管理平台','连接产品、研发与测试，建立清晰、可追溯的内部项目协作流程。'),('客户服务中心','统一客户服务入口，提升工单响应与服务交付体验。'),('数据洞察与报表','让业务数据成为团队决策的可靠依据。')]):
            project=Project.objects.create(name=name,description=desc,owner=user)
            for role,_ in ROLES: Membership.objects.create(project=project,user=user,role=role)
            Membership.objects.create(project=project,user=developer,role='开发人员'); Membership.objects.create(project=project,user=tester,role='测试负责人')
            titles=[('工作台待办与逾期提醒','5','P1',-2),('项目成员与角色权限管理','4','P1',3),('需求历史版本与附件归档','2','P2',6),('代码上传与审核记录','7','P2',-1),('站内通知中心','1','P3',9),('独立账号登录与停用','10','P2',-5)] if index==0 else ([('工单流转与处理记录','3','P1',5),('客户信息导入与校验','10','P2',2)] if index==1 else [('项目交付效率统计','4','P2',4),('月度报表导出','0','P3',10)])
            for title,stage,priority,days in titles:
                req=Requirement.objects.create(project=project,title=title,body=f'业务目标\n{title}，让团队快速掌握进展并保留完整操作记录。\n\n验收标准\n1. 仅授权项目成员可访问数据。\n2. 信息准确展示，操作后及时更新。\n3. 异常场景提供明确提示，历史记录保留。',stage=stage,priority=priority,owner=user,creator=user,due_date=timezone.localdate()+timedelta(days=days))
                rec=Record.objects.create(requirement=req,stage=stage,round=1,action='演示数据初始化',actor=user,owner=user,note='演示场景：请通过处理窗口继续推进流程。',snapshot=snapshot(req),incomplete=True)
                Audit.objects.create(actor=user,project=project,action='创建需求',detail=str(req))
                if stage=='5':
                    bug=Bug.objects.create(requirement=req,test_record=rec,round=1,title='逾期事项在日期边界未及时刷新',steps='1. 打开工作台\n2. 将需求截止日期设为昨日\n3. 查看逾期统计',actual='逾期统计未刷新',expected='显示正确的逾期数量',environment='Chrome / macOS',discovered_version='v0.8.0',severity='major',priority='P1',owner=developer)
                    BugEvent.objects.create(bug=bug,actor=user,action='创建 Bug',note=bug.steps,version='v0.8.0',previous_owner=developer,receiver=developer)
            Notification.objects.create(user=user,text=f'欢迎加入 {project.name}，查看项目需求与待办。',url=f'/projects/{project.pk}/')
        self.stdout.write(self.style.SUCCESS(f'演示数据已创建。账号：demo  密码：{password}'))
