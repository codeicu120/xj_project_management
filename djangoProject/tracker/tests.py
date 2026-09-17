import tempfile
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from .models import *
from .services import process, act_bug, snapshot
User=get_user_model()
class WorkflowTests(TestCase):
    def setUp(self):
        self.user=User.objects.create_user('owner',password='Test-password-2026')
        self.other=User.objects.create_user('outsider',password='Test-password-2026')
        self.project=Project.objects.create(name='测试项目',owner=self.user)
        for role,_ in ROLES: Membership.objects.create(project=self.project,user=self.user,role=role)
        self.req=Requirement.objects.create(project=self.project,title='权限与流程',body='原始版本',owner=self.user,creator=self.user)
    def data(self,**kw):
        d={'action':'advance','receiver':self.user,'note':'通过','revision':self.req.records.count(),'commit':'','version':'','risk_reason':'','body':'','target':'','file':None}; d.update(kw); return d
    def bug(self):
        self.req.stage='5'; self.req.save()
        rec=Record.objects.create(requirement=self.req,stage='5',round=1,actor=self.user,owner=self.user,action='测试',note='问题',snapshot=snapshot(self.req))
        return Bug.objects.create(requirement=self.req,test_record=rec,round=1,title='测试 Bug',steps='复现',actual='错误',expected='成功',environment='测试环境',discovered_version='v1',owner=self.user)
    def bug_data(self,bug,**kw):
        d={'action':'fix','note':'修复说明','commit':'abc123','version':'v2','revision':bug.events.count(),'receiver':self.user,'file':None}; d.update(kw); return d
    def test_full_workflow(self):
        for index in range(10):
            process(self.user,self.req.pk,self.data(commit='abc123' if index in [7,8] else ''))
        self.req.refresh_from_db(); self.assertEqual(self.req.stage,'10'); self.assertEqual(self.req.records.count(),10)
        self.assertEqual(self.req.records.filter(stage__in=['5','6']).count(),2)
    def test_return_preserves_snapshot_and_round(self):
        self.req.stage='5'; self.req.save()
        rec=process(self.user,self.req.pk,self.data(action='return',target='1',body='新版本',note='规则需补充'))
        self.req.refresh_from_db(); self.assertEqual((self.req.stage,self.req.round,self.req.body),('1',2,'新版本'))
        self.assertEqual(rec.snapshot['body'],'原始版本'); self.assertEqual(rec.round,1)
    def test_return_requires_upstream(self):
        with self.assertRaises(ValidationError): process(self.user,self.req.pk,self.data(action='return',target='4'))
    def test_stale_request_rejected(self):
        data=self.data(); process(self.user,self.req.pk,data)
        with self.assertRaises(ValidationError): process(self.user,self.req.pk,data)
    def test_cross_project_processing_forbidden(self):
        with self.assertRaises(PermissionDenied): process(self.other,self.req.pk,self.data())
    def test_role_required(self):
        self.project.memberships.all().delete()
        with self.assertRaises(PermissionDenied): process(self.user,self.req.pk,self.data())
    def test_invalid_receiver_role(self):
        Membership.objects.create(project=self.project,user=self.other,role='开发人员')
        with self.assertRaises(ValidationError): process(self.user,self.req.pk,self.data(receiver=self.other))
    def test_open_bugs_require_risk_reason(self):
        self.bug()
        with self.assertRaises(ValidationError): process(self.user,self.req.pk,self.data())
        rec=process(self.user,self.req.pk,self.data(risk_reason='低风险问题下轮处理')); self.assertIn('低风险',rec.note)
    def test_code_review_matches_upload(self):
        self.req.stage='7'; self.req.save()
        process(self.user,self.req.pk,self.data(commit='abc'))
        with self.assertRaises(ValidationError): process(self.user,self.req.pk,self.data(commit='different'))
        process(self.user,self.req.pk,self.data(commit='abc'))
    def test_bug_fix_regress_reopen_history(self):
        bug=self.bug(); act_bug(self.user,bug.pk,self.bug_data(bug))
        act_bug(self.user,bug.pk,self.bug_data(bug,action='pass'))
        act_bug(self.user,bug.pk,self.bug_data(bug,action='fail'))
        bug.refresh_from_db(); self.assertEqual(bug.status,'open'); self.assertEqual(bug.events.count(),3)
    def test_regression_failure_then_fix(self):
        bug=self.bug(); act_bug(self.user,bug.pk,self.bug_data(bug)); act_bug(self.user,bug.pk,self.bug_data(bug,action='fail')); act_bug(self.user,bug.pk,self.bug_data(bug,version='v3'))
        self.assertEqual(bug.events.count(),3)
    def test_bug_fix_requires_commit(self):
        bug=self.bug()
        with self.assertRaises(ValidationError): act_bug(self.user,bug.pk,self.bug_data(bug,commit=''))
    def test_regression_requires_test_role(self):
        bug=self.bug(); act_bug(self.user,bug.pk,self.bug_data(bug))
        self.project.memberships.filter(role='测试负责人').delete()
        with self.assertRaises(PermissionDenied): act_bug(self.user,bug.pk,self.bug_data(bug,action='pass'))
    def test_cannot_close_without_fix(self):
        bug=self.bug()
        with self.assertRaises(ValidationError): act_bug(self.user,bug.pk,self.bug_data(bug,action='pass'))
    def test_archive_read_only(self):
        self.project.archived=True; self.project.save()
        with self.assertRaises(ValidationError): process(self.user,self.req.pk,self.data())
    def test_incomplete_material_recorded(self):
        rec=process(self.user,self.req.pk,self.data()); self.assertTrue(rec.incomplete)
    def test_pages_render_and_isolate(self):
        bug=self.bug(); self.client.force_login(self.user)
        for url in ['/','/projects/','/projects/1/','/requirements/','/requirements/new/','/requirements/1/','/bugs/','/bugs/1/','/requirements/1/bugs/new/','/notifications/','/logs/','/requirements/?start=bad&owner=bad','/bugs/?project=bad']:
            with self.subTest(url=url): self.assertEqual(self.client.get(url).status_code,200)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get('/requirements/1/').status_code,404)
        self.assertEqual(self.client.get('/bugs/1/').status_code,403)
        self.assertNotContains(self.client.get('/requirements/'),'权限与流程')
    def test_login_and_inactive_user(self):
        self.assertEqual(self.client.get('/').status_code,302)
        self.user.is_active=False; self.user.save()
        self.assertFalse(self.client.login(username='owner',password='Test-password-2026'))
    def test_csrf_required(self):
        from django.test import Client
        client=Client(enforce_csrf_checks=True); client.force_login(self.user)
        self.assertEqual(client.post('/requirements/1/',{}).status_code,403)
    def test_attachments_protected_and_versioned(self):
        with tempfile.TemporaryDirectory() as folder, override_settings(MEDIA_ROOT=folder):
            file=SimpleUploadedFile('sample.txt',b'material')
            process(self.user,self.req.pk,self.data(action='note',file=file))
            item=Attachment.objects.get(); self.assertEqual(item.round,1)
            self.client.force_login(self.other); self.assertEqual(self.client.get(f'/attachments/{item.pk}/').status_code,403)
            self.client.force_login(self.user); response=self.client.get(f'/attachments/{item.pk}/'); self.assertEqual(response.status_code,200); response.close()
    def test_large_attachment_rejected_without_record(self):
        with override_settings(MAX_ATTACHMENT_SIZE=3):
            with self.assertRaises(ValidationError): process(self.user,self.req.pk,self.data(file=SimpleUploadedFile('large.txt',b'1234')))
        self.assertEqual(self.req.records.count(),0)
    def test_admin_history_readonly(self):
        admin=User.objects.create_superuser('admin',password='Test-password-2026'); self.client.force_login(admin)
        self.assertEqual(self.client.post('/admin/tracker/requirement/1/change/',{'title':'tamper'}).status_code,403)
    def test_field_edit_keeps_snapshot_and_audit(self):
        rec=process(self.user,self.req.pk,self.data(action='note',title='新标题',priority='P0'))
        self.req.refresh_from_db(); self.assertEqual(self.req.title,'新标题'); self.assertEqual(rec.snapshot['title'],'权限与流程')
        self.assertTrue(Audit.objects.filter(action='修改需求字段').exists())
    def test_create_requirement_post(self):
        self.client.force_login(self.user)
        response=self.client.post('/requirements/new/',{'project':self.project.pk,'title':'新需求','body':'验收标准','priority':'P2','owner':self.user.pk,'due_date':'2026-10-01'})
        self.assertEqual(response.status_code,302); self.assertTrue(Requirement.objects.filter(title='新需求').exists())
    def test_bug_attachment_keeps_original_test_round(self):
        bug=self.bug(); self.req.round=2; self.req.stage='4'; self.req.save()
        with tempfile.TemporaryDirectory() as folder, override_settings(MEDIA_ROOT=folder):
            act_bug(self.user,bug.pk,self.bug_data(bug,file=SimpleUploadedFile('fix.txt',b'fix')))
            attachment=Attachment.objects.get(); self.assertEqual((attachment.stage,attachment.round),('5',1))
    def test_notification_read_post_only(self):
        self.client.force_login(self.user); note=Notification.objects.create(user=self.user,text='待办',url='/')
        self.assertEqual(self.client.get('/notifications/read/').status_code,405)
        self.client.post('/notifications/read/'); note.refresh_from_db(); self.assertTrue(note.read)
    def test_bug_create_post(self):
        self.req.stage='5'; self.req.save(); self.client.force_login(self.user)
        response=self.client.post(f'/requirements/{self.req.pk}/bugs/new/',{'title':'界面异常','steps':'点击按钮','actual':'无响应','expected':'有响应','environment':'Chrome','discovered_version':'v1','severity':'normal','priority':'P2','owner':self.user.pk})
        self.assertEqual(response.status_code,302); bug=Bug.objects.get(); self.assertEqual(bug.test_record.round,1); self.assertEqual(bug.events.count(),1)
