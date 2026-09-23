import hashlib
import io
import json
import tempfile
from datetime import timedelta
from decimal import Decimal
from django.test import TestCase, Client, override_settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from .models import *
from .recorder_services import *
from .reports import make_snapshot, export_pdf, export_csv, export_materials, export_xlsx

User=get_user_model()
class RecorderTests(TestCase):
    def setUp(self):
        self.recorder=User.objects.create_user('recorder',password='Test-password-2026')
        self.viewer=User.objects.create_user('developer',is_staff=True)
        self.other=User.objects.create_user('outsider')
        self.project=Project.objects.create(name='产品工作空间',owner=self.recorder)
        Membership.objects.create(project=self.project,user=self.recorder,role='记录人员')
        Membership.objects.create(project=self.project,user=self.viewer,role='开发人员')
        self.req=create_requirement(self.recorder,self.req_data())
        self.client.force_login(self.recorder)
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.media=override_settings(MEDIA_ROOT=self.temp.name); self.media.enable(); self.addCleanup(self.media.disable)
    def req_data(self,**kw):
        data=dict(project=self.project,title='离线业务需求',body='验收范围',priority='P2',proposer='群昵称小林',business_owner='不需要账号的开发',version='v1',source_group='项目协作群',due_date=None,conclusion='pending'); data.update(kw); return data
    def progress(self,**kw):
        data=dict(revision=self.req.records.count(),stage='4',business_owner='陈启',note='接口完成',version='v1',save_mode='append',round_mode='keep',conclusion='pending'); data.update(kw); return data
    def bug_data(self,**kw):
        data=dict(requirement=self.req,title='线下问题',discovery_stage='0',status='open',business_owner='外部开发',provider='测试昵称',note='问题说明',discovered_version='v1',source='群聊天'); data.update(kw); return data
    def daily_data(self,**kw):
        data=dict(project=self.project,day=timezone.localdate(),channel='搜索',provider='运营小王',visits=120,registrations=20,cost=Decimal('10'),revenue=None,source='日报.xlsx',note='',verified=False); data.update(kw); return data
    def image(self):
        from PIL import Image
        out=io.BytesIO(); Image.new('RGB',(200,100),'white').save(out,format='PNG')
        return SimpleUploadedFile('群聊.png',out.getvalue(),content_type='image/png')
    def report(self,**kw):
        data=dict(kind='weekly',start=timezone.localdate()-timedelta(days=6),end=timezone.localdate(),include_materials=True); data.update(kw)
        return Report.objects.create(actor=self.recorder,kind=data['kind'],start=data['start'],end=data['end'],snapshot=make_snapshot(Project.objects.filter(pk=self.project.pk),data))
    def test_names_do_not_require_login_accounts(self):
        self.assertEqual(self.req.business_owner,'不需要账号的开发'); self.assertIsNone(self.req.owner)
        self.assertEqual(self.req.creator,self.recorder); self.assertEqual(User.objects.count(),3)
    def test_only_recorders_may_write(self):
        for user in [self.viewer,self.other]:
            with self.assertRaises(PermissionDenied): create_requirement(user,self.req_data())
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.post(f'/requirements/{self.req.pk}/',self.progress()).status_code,403)
    def test_append_does_not_change_stage_owner_or_round(self):
        record_progress(self.recorder,self.req.pk,self.progress())
        self.req.refresh_from_db(); self.assertEqual(self.req.stage,'0'); self.assertEqual(self.req.business_owner,'不需要账号的开发'); self.assertEqual(self.req.round,1)
        self.assertEqual(self.req.records.first().payload['business_owner'],'陈启')
    def test_update_and_rework_preserve_snapshots(self):
        record_progress(self.recorder,self.req.pk,self.progress(save_mode='update',stage='10'))
        self.req.refresh_from_db(); self.assertEqual(self.req.stage,'10')
        record_progress(self.recorder,self.req.pk,self.progress(save_mode='update',stage='1',round_mode='new'))
        self.req.refresh_from_db(); self.assertEqual(self.req.round,2); self.assertEqual(self.req.records.first().snapshot['stage'],'10')
    def test_completed_requirement_can_add_historical_material(self):
        record_progress(self.recorder,self.req.pk,self.progress(save_mode='update',stage='10'))
        self.req.refresh_from_db()
        record_progress(self.recorder,self.req.pk,self.progress(stage='10',evidence_stage='6',confirmer='需求方',summary='明确验收通过',conclusion='confirmed',checked=True,screenshots=[self.image()]))
        self.req.refresh_from_db(); self.assertEqual(self.req.stage,'10'); self.assertEqual(evidence(self.req)['covered'],1)
        self.assertEqual(self.req.attachments.get().stage,'6')
    def test_upload_alone_not_confirmation(self):
        record_progress(self.recorder,self.req.pk,self.progress(stage='0',screenshots=[self.image()]))
        self.assertEqual(evidence(self.req)['covered'],0)
    def test_confirmation_requires_evidence_and_explicit_result(self):
        with self.assertRaises(ValidationError): record_progress(self.recorder,self.req.pk,self.progress(stage='0',checked=True,confirmer='甲',summary='同意',conclusion='confirmed'))
        record_progress(self.recorder,self.req.pk,self.progress(stage='0',checked=True,confirmer='甲',summary='同意',conclusion='confirmed',screenshots=[self.image()]))
        self.assertEqual(evidence(self.req)['covered'],1)
        record_progress(self.recorder,self.req.pk,self.progress(stage='0',confirmer='甲',summary='需再修改',conclusion='rejected'))
        self.assertEqual(evidence(self.req)['covered'],0); self.assertEqual(self.req.confirmations.count(),2)
    def test_old_round_does_not_fill_new_round(self):
        record_progress(self.recorder,self.req.pk,self.progress(stage='0',checked=True,confirmer='甲',summary='同意',conclusion='confirmed',screenshots=[self.image()]))
        record_progress(self.recorder,self.req.pk,self.progress(stage='0',round_mode='new',save_mode='update'))
        self.req.refresh_from_db(); self.assertEqual(evidence(self.req)['covered'],0)
    def test_occurrence_and_recording_times_are_separate(self):
        past=timezone.now()-timedelta(days=5)
        record_progress(self.recorder,self.req.pk,self.progress(occurred_at=past))
        rec=self.req.records.first(); self.assertEqual(rec.occurred_at,past); self.assertGreater(rec.created_at,rec.occurred_at)
    def test_bug_creation_outside_test_and_reopening(self):
        bug=record_bug(self.recorder,self.bug_data(status='closed'))
        self.assertEqual(bug.status,'closed'); self.assertIsNone(bug.test_record)
        record_bug(self.recorder,self.bug_data(status='open',revision=bug.events.count()),bug.pk)
        bug.refresh_from_db(); self.assertEqual(bug.events.count(),2); self.assertEqual(bug.events.first().action,'重新打开')
    def test_bug_cross_project_cannot_reparent(self):
        bug=record_bug(self.recorder,self.bug_data())
        req2=create_requirement(self.recorder,self.req_data(title='另一个需求'))
        with self.assertRaises(ValidationError): record_bug(self.recorder,self.bug_data(requirement=req2,revision=1),bug.pk)
    def test_stale_mutation_rejected(self):
        data=self.progress(); record_progress(self.recorder,self.req.pk,data)
        with self.assertRaises(ValidationError): record_progress(self.recorder,self.req.pk,data)
    def test_attachment_permissions_and_validation(self):
        record_progress(self.recorder,self.req.pk,self.progress(stage='0',screenshots=[self.image()],documents=[SimpleUploadedFile('规则.txt',b'rules')]))
        self.assertEqual(self.req.attachments.count(),2)
        self.client.force_login(self.other); self.assertEqual(self.client.get(f'/attachments/{self.req.attachments.first().pk}/').status_code,403)
        with self.assertRaises(ValidationError): validate_materials([SimpleUploadedFile('fake.png',b'bad')],[])
        with self.assertRaises(ValidationError): validate_materials([], [SimpleUploadedFile('x.exe',b'bad')])
    def test_daily_unique_and_missing_values(self):
        row=create_entity(self.recorder,'data',self.daily_data(visits=None,cost=None))
        with self.assertRaises(ValidationError): create_entity(self.recorder,'data',self.daily_data())
        report=self.report(); self.assertEqual(report.snapshot['pending_data'],1); self.assertIsNone(report.snapshot['totals']['visits'])
        update_entity(self.recorder,'data',row.pk,self.daily_data(visits=None,cost=None,revision=1,verified=True))
        self.assertIsNone(self.report().snapshot['totals']['visits'])
    def test_daily_correction_requires_reason_and_preserves_original(self):
        row=create_entity(self.recorder,'data',self.daily_data())
        with self.assertRaises(ValidationError): update_entity(self.recorder,'data',row.pk,self.daily_data(revision=1,visits=999))
        update_entity(self.recorder,'data',row.pk,self.daily_data(revision=1,visits=999,reason='原表更正',verified=True))
        events=Journal.objects.filter(entity_type='data',entity_id=row.pk)
        self.assertEqual(events.count(),2); self.assertEqual(events.first().snapshot['before']['visits'],120)
        self.assertEqual(self.report().snapshot['totals']['visits'],999)
    def test_anomaly_is_append_only_and_missing_is_not_zero(self):
        obj=create_entity(self.recorder,'anomalies',dict(project=self.project,title='波动',metric='注册数',provider='运营',source='报表',description='尚待核查',before_value=None,after_value=0))
        self.assertIsNone(obj.change_percent)
        update_entity(self.recorder,'anomalies',obj.pk,dict(revision=1,provider='测试',status='done',source='群消息',note='暂不能确定原因'))
        obj.refresh_from_db(); self.assertEqual(obj.description,'尚待核查'); self.assertEqual(Journal.objects.filter(entity_type='anomalies',entity_id=obj.pk).count(),2)
    def test_asset_renewal_requires_verification(self):
        today=timezone.localdate()
        obj=create_entity(self.recorder,'assets',dict(project=self.project,name='域名',kind='domain',identifier='example.test',supplier='注册商',business_owner='管理员',expires_on=today,cost=Decimal('20'),purpose='访问'))
        data=dict(revision=1,provider='办理人',handled_on=today,expires_on=today+timedelta(days=365),cost=Decimal('25'),source='付款单',note='续费',verified=False)
        with self.assertRaises(ValidationError): update_entity(self.recorder,'assets',obj.pk,data)
        data['verified']=True; update_entity(self.recorder,'assets',obj.pk,data)
        event=Journal.objects.filter(entity_type='assets',entity_id=obj.pk).first(); self.assertEqual(event.snapshot['before']['expires_on'],today.isoformat())
    def test_review_advice_and_decision_separate(self):
        obj=create_entity(self.recorder,'reviews',dict(project=self.project,title='建议',version='v1',provider='开发',category='backend',risk='medium',description='建议修改',reproduction='复现说明'))
        update_entity(self.recorder,'reviews',obj.pk,dict(revision=1,provider='负责人',status='deferred',source='群确认',note='下次安排'))
        obj.refresh_from_db(); self.assertEqual(obj.description,'建议修改'); self.assertEqual(obj.status,'deferred')
    def test_archive_blocks_writes(self):
        self.project.archived=True; self.project.save()
        with self.assertRaises(PermissionDenied): record_progress(self.recorder,self.req.pk,self.progress())
    def test_report_snapshot_immutable_and_access_isolated(self):
        report=self.report(); old=report.snapshot
        Requirement.objects.filter(pk=self.req.pk).update(title='变化了')
        self.client.post(f'/reports/{report.pk}/archive/')
        report.refresh_from_db(); self.assertEqual(report.snapshot,old); self.assertIsNotNone(report.archived_at)
        self.client.force_login(self.other); self.assertEqual(self.client.get(f'/reports/{report.pk}/').status_code,403)
    def test_exports_pdf_excel_and_zip(self):
        record_progress(self.recorder,self.req.pk,self.progress(stage='0',screenshots=[self.image()]))
        report=self.report()
        self.assertTrue(export_pdf(report).startswith(b'%PDF'))
        self.assertTrue(export_xlsx(report).startswith(b'PK'))
        self.assertTrue(export_materials(report).startswith(b'PK'))
        self.assertIn('离线业务需求',export_csv(report).decode('utf-8-sig'))
    def test_report_date_filter_and_no_unverified_total(self):
        create_entity(self.recorder,'data',self.daily_data(verified=True))
        create_entity(self.recorder,'data',self.daily_data(day=timezone.localdate()-timedelta(days=20),channel='旧渠道',visits=500,verified=True))
        create_entity(self.recorder,'data',self.daily_data(channel='待核对',visits=800))
        report=self.report(); self.assertEqual(report.snapshot['totals']['visits'],120); self.assertEqual(len(report.snapshot['data']),1)
    def test_pages_render_all_modules(self):
        bug=record_bug(self.recorder,self.bug_data())
        urls=['/','/requirements/','/requirements/new/',f'/requirements/{self.req.pk}/','/bugs/','/bugs/new/',f'/bugs/{bug.pk}/','/records/reviews/','/records/reviews/new/','/records/data/','/records/data/new/','/records/anomalies/','/records/anomalies/new/','/records/assets/','/records/assets/new/','/connections/','/reports/','/projects/','/logs/']
        for url in urls:
            with self.subTest(url=url): self.assertEqual(self.client.get(url).status_code,200)
    def test_create_and_progress_forms_post(self):
        response=self.client.post('/requirements/new/',dict(project=self.project.pk,title='表单创建',priority='P2',proposer='甲',business_owner='乙',body='背景',conclusion='pending'))
        self.assertEqual(response.status_code,302)
        req=Requirement.objects.get(title='表单创建')
        response=self.client.post(f'/requirements/{req.pk}/',dict(revision=req.records.count(),stage='10',business_owner='乙',note='线下已完成',save_mode='update',round_mode='keep',conclusion='pending'))
        self.assertEqual(response.status_code,302); req.refresh_from_db(); self.assertEqual(req.stage,'10')
    def test_csrf_and_https_origin(self):
        client=Client(enforce_csrf_checks=True); client.force_login(self.recorder)
        self.assertEqual(client.post('/requirements/new/',{}).status_code,403)
        client.logout(); client.get('/login/',HTTP_HOST='project.xjdev.one')
        response=client.post('/login/',{'username':'recorder','password':'Test-password-2026','csrfmiddlewaretoken':client.cookies['csrftoken'].value},HTTP_HOST='project.xjdev.one',HTTP_ORIGIN='https://project.xjdev.one')
        self.assertEqual(response.status_code,302)
    def test_viewer_admin_link_hidden_and_cross_project_read_denied(self):
        self.client.force_login(self.viewer); self.assertNotContains(self.client.get('/'),'href="/admin/"')
        self.client.force_login(self.other); self.assertEqual(self.client.get(f'/requirements/{self.req.pk}/').status_code,404)
    def connection(self):
        token='only-test-token'; return DataConnection.objects.create(project=self.project,name='日报源',token_hash=hashlib.sha256(token.encode()).hexdigest()),token
    def test_ingest_idempotency_and_conflicts(self):
        connection,token=self.connection()
        payload={'request_id':'one','rows':[{'day':timezone.localdate().isoformat(),'channel':'渠道','visits':100,'cost':'12.00'}]}
        ingest(connection,payload); ingest(connection,payload)
        self.assertEqual(DailyData.objects.count(),1); self.assertEqual(SyncRun.objects.count(),1)
        row=DailyData.objects.get(); self.assertEqual(row.status,'pending')
        row.status='verified'; row.save(); payload['request_id']='two'; payload['rows'][0]['visits']=150
        run=ingest(connection,payload); row.refresh_from_db(); self.assertEqual(row.visits,100); self.assertEqual(row.status,'conflict'); self.assertEqual(run.conflicts,1)
        self.assertEqual(Journal.objects.filter(entity_type='data',entity_id=row.pk).first().payload['incoming']['visits'],150)
    def test_ingest_auth_and_invalid_batch_rollback(self):
        connection,token=self.connection()
        self.assertEqual(self.client.post('/api/operations/ingest/',data='{}',content_type='application/json').status_code,401)
        payload={'request_id':'bad','rows':[{'day':'2026-09-23','channel':'渠道'},{'day':'bad','channel':'其他'}]}
        response=self.client.post('/api/operations/ingest/',data=json.dumps(payload),content_type='application/json',HTTP_AUTHORIZATION='Bearer '+token)
        self.assertEqual(response.status_code,400); self.assertEqual(DailyData.objects.count(),0); self.assertTrue(SyncRun.objects.filter(status='error').exists())
    def test_superuser_and_recorder_only_project_permissions(self):
        admin=User.objects.create_superuser('admin',password='Test-password-2026'); self.client.force_login(admin)
        self.assertContains(self.client.get('/'),'href="/admin/"')
        self.assertEqual(self.client.post(f'/admin/tracker/requirement/{self.req.pk}/change/',{'title':'篡改'}).status_code,403)
    def test_report_keeps_append_history_and_missing_counts(self):
        anomaly=create_entity(self.recorder,'anomalies',dict(project=self.project,title='指标变化',metric='访问',before_value=100,after_value=80,provider='运营',source='日报',description='原因待核查'))
        update_entity(self.recorder,'anomalies',anomaly.pk,dict(revision=1,status='done',provider='运营',source='群反馈',note='已核对渠道记录，尚无因果结论'))
        create_entity(self.recorder,'data',self.daily_data(visits=None,cost=None,verified=True))
        report=self.report()
        self.assertEqual(report.snapshot['missing_counts']['visits'],1)
        self.assertIsNone(report.snapshot['totals']['visits'])
        self.assertIn('尚无因果结论',json.dumps(report.snapshot['histories'],ensure_ascii=False))
        update_entity(self.recorder,'anomalies',anomaly.pk,dict(revision=2,status='working',provider='运营',source='群反馈',note='继续调查'))
        report.refresh_from_db(); self.assertNotIn('继续调查',json.dumps(report.snapshot,ensure_ascii=False))
    def test_ingest_equivalent_decimal_and_changed_request_id(self):
        connection,token=self.connection()
        payload={'request_id':'a','rows':[{'day':'2026-09-23','channel':'渠道','cost':'12.00'}]}
        ingest(connection,payload)
        row=DailyData.objects.get(); row.status='verified'; row.save()
        payload['request_id']='b'; payload['rows'][0]['cost']='12'
        self.assertEqual(ingest(connection,payload).conflicts,0)
        row.refresh_from_db(); self.assertEqual(row.status,'verified')
        payload['rows'][0]['cost']='13'
        with self.assertRaises(ValidationError): ingest(connection,payload)
        connection.enabled=False; connection.save()
        self.assertEqual(self.client.post('/api/operations/ingest/',data=json.dumps(payload),content_type='application/json',HTTP_AUTHORIZATION='Bearer '+token).status_code,401)
    def test_daily_form_correction_and_choice_summary(self):
        data={k:('' if v is None else v) for k,v in self.daily_data().items()}; data['project']=self.project.pk; data['day']=data['day'].isoformat()
        response=self.client.post('/records/data/new/',data)
        self.assertEqual(response.status_code,302)
        obj=DailyData.objects.get(); data.update(revision=1,visits=200,reason='原表更正',verified=True)
        response=self.client.post(f'/records/data/{obj.pk}/',data)
        self.assertEqual(response.status_code,302); obj.refresh_from_db(); self.assertEqual(obj.visits,200)
        self.assertContains(self.client.get(f'/records/data/{obj.pk}/'),'已核对')
    def test_selected_readonly_project_hides_write_links(self):
        project=Project.objects.create(name='只读项目',owner=self.other)
        Membership.objects.create(project=project,user=self.recorder,role='开发人员')
        self.assertNotContains(self.client.get(f'/?project={project.pk}'),'＋ 登记新需求')
        response=self.client.post('/reports/',{'kind':'weekly','start':timezone.localdate(),'end':timezone.localdate()})
        self.assertEqual(response.status_code,302)
        self.assertEqual(Report.objects.get().snapshot['project_ids'],[self.project.pk])
    def test_report_download_routes_and_archive(self):
        report=self.report()
        for format in ['pdf','xlsx','csv','zip']:
            response=self.client.get(f'/reports/{report.pk}/download/{format}/')
            self.assertEqual(response.status_code,200); self.assertIn('attachment;',response['Content-Disposition'])
        self.client.post(f'/reports/{report.pk}/archive/'); report.refresh_from_db(); self.assertIsNotNone(report.archived_at)
