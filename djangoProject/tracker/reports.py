"""Report snapshots are generated once; exports never query changing business values."""
import csv
import io
import json
import zipfile
from pathlib import Path
from decimal import Decimal
from html import escape
from django.db.models import Q
from django.core.exceptions import ValidationError
from django.utils import timezone
from .models import *
from .recorder_services import evidence, serial


def make_snapshot(projects,data):
    start,end=data['start'],data['end']
    qs=Requirement.objects.filter(project__in=projects,created_at__date__lte=end)
    if data['kind']=='requirement': qs=qs.filter(pk=data['requirement'].pk)
    else: qs=qs.filter(Q(updated_at__date__range=(start,end))|Q(records__created_at__date__gte=start,records__created_at__date__lte=end)).distinct()
    reqs=[]
    for req in qs.select_related('project'):
        ev=evidence(req)
        reqs.append({'id':req.pk,'number':req.number,'title':req.title,'project':req.project.name,'owner':req.business_owner,'proposer':req.proposer,'stage':req.get_stage_display(),'round':req.round,'version':req.version,'covered':ev['covered'],'required':ev['required'],'missing':ev['missing']})
    progress=[{'requirement':r.requirement.number,'note':r.note,'stage':r.get_stage_display(),'round':r.round,'version':r.version,'occurred_at':r.occurred_at,'recorded_at':r.created_at,'recorder':str(r.actor),'details':r.payload} for r in Record.objects.filter(requirement__in=qs,created_at__date__range=(start,end)).select_related('requirement','actor')]
    bugs=[{'number':b.number,'title':b.title,'requirement':b.requirement.number,'owner':b.business_owner,'status':b.get_status_display(),'version':b.fix_version or b.discovered_version} for b in Bug.objects.filter(requirement__in=qs,created_at__date__lte=end).select_related('requirement')]
    daily=DailyData.objects.filter(project__in=projects,day__range=(start,end))
    rows=[{'project':r.project.name,'day':r.day,'channel':r.channel,'visits':r.visits,'registrations':r.registrations,'cost':r.cost,'revenue':r.revenue,'source':r.source,'source_type':r.get_source_type_display(),'provider':r.provider} for r in daily.filter(status='verified').select_related('project')]
    def total(key):
        values=[row[key] for row in rows if row[key] is not None]
        return sum(values) if values else None
    attachments=[]
    if data.get('include_materials'):
        attachments=[{'id':a.pk,'name':a.name,'kind':a.kind,'stage':a.get_stage_display(),'round':a.round,'version':a.version,'requirement':a.requirement.number} for a in Attachment.objects.filter(requirement__in=qs,created_at__date__lte=end).select_related('requirement')]
    reviews=[{'title':r.title,'version':r.version,'risk':r.get_risk_display(),'status':r.get_status_display(),'description':r.description,'provider':r.provider,'reproduction':r.reproduction} for r in Review.objects.filter(project__in=projects,created_at__date__lte=end)]
    anomalies=[{'title':a.title,'metric':a.metric,'before':a.before_value,'after':a.after_value,'change':a.change_percent,'status':a.get_status_display(),'source':a.source,'description':a.description} for a in Anomaly.objects.filter(project__in=projects,created_at__date__lte=end)]
    assets=[{'name':a.name,'owner':a.business_owner,'expires_on':a.expires_on,'purpose':a.purpose} for a in Asset.objects.filter(project__in=projects,expires_on__lte=end+timezone.timedelta(days=30))]
    histories=[]
    for kind in ['reviews','anomalies','assets']:
        for event in Journal.objects.filter(project__in=projects,entity_type=kind,created_at__date__range=(start,end)).select_related('actor','project'):
            histories.append({'entity':kind,'entity_id':event.entity_id,'project':event.project.name,'action':event.action,'recorder':str(event.actor),'recorded_at':event.created_at,'payload':event.payload,'snapshot':event.snapshot})
    return serial({'histories':histories,'missing_counts':{key:sum(row[key] is None for row in rows) for key in ['visits','registrations','cost','revenue']},'project_ids':list(projects.values_list('pk',flat=True)),'projects':list(projects.values_list('name',flat=True)),'start':start,'end':end,'generated_at':timezone.now(),'requirements':reqs,'progress':progress,'bugs':bugs,'data':rows,'pending_data':daily.exclude(status='verified').count(),'totals':{'visits':total('visits'),'cost':total('cost')},'reviews':reviews,'anomalies':anomalies,'assets':assets,'attachments':attachments,'include_materials':data.get('include_materials',False),'scope_note':'进展按录入日期筛选，运营数据按统计日筛选；需求、审查、波动与资产状态为生成时快照，不追溯推算期末状态。仅已核对数据计入汇总；未知数据不按零计算。'})

def report_sections(s):
    return [
        ('需求进展与确认材料',[(r['number']+' · '+r['title'],f"{r['project']} · 负责人 {r['owner']} · {r['stage']} · 第 {r['round']} 轮\n材料 {r['covered']}/{r['required']}；待补："+'、'.join(r['missing'])) for r in s['requirements']]),
        ('进度日志',[(r['requirement']+' · '+r['stage'],r['note']+'\n已完成：'+str(r['details'].get('completed',''))+'；待处理：'+str(r['details'].get('blockers',''))+'；下一步：'+str(r['details'].get('next_step',''))+f"\n业务发生：{r['occurred_at'] or '不详'}；录入：{r['recorded_at']}；录入人：{r['recorder']}") for r in s['progress']]),
        ('关联 Bug',[(r['number']+' · '+r['title'],f"{r['status']} · {r['owner']} · 版本 {r['version']}") for r in s['bugs']]),
        ('运营数据与来源',[(str(r['day'])+' · '+r['channel'],f"访问 {display(r['visits'])} / 注册 {display(r['registrations'])} / 费用 USD {display(r['cost'])}\n{r['source_type']} · {r['provider']} · {r['source']}") for r in s['data']]),
        ('版本与审查建议',[(r['title'],f"{r['version']} · {r['status']} · {r['provider']}\n{r['description']}\n复现：{r['reproduction']}") for r in s['reviews']]),
        ('波动核查',[(r['title'],f"{r['metric']}：{display(r['before'])} → {display(r['after'])}；{r['status']}\n{r['description']}\n来源：{r['source']}") for r in s['anomalies']]),
        ('核查与续费历史',[(r['project']+' · '+r['action'],f"{r['entity']} #{r['entity_id']} · 录入 {r['recorder']} / {r['recorded_at']}\n"+json.dumps(r['payload'],ensure_ascii=False)) for r in s.get('histories',[])]),
        ('资产与到期提醒',[(r['name'],f"{r['owner']} · 到期日 {r['expires_on']} · {r['purpose']}") for r in s['assets']]),
    ]
def display(value): return '不详' if value is None else str(value)

def export_pdf(report):
    from reportlab.pdfgen import canvas
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, PageBreak
    from reportlab.lib.enums import TA_LEFT
    if 'NotoSansSC' not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont('NotoSansSC',str(Path(__file__).parent / 'fonts' / 'NotoSansSC.ttf')))
    out=io.BytesIO(); s=report.snapshot
    styles=getSampleStyleSheet()
    body=ParagraphStyle('CN',fontName='NotoSansSC',fontSize=10,leading=16,wordWrap='CJK',spaceAfter=8,textColor=colors.HexColor('#384157'))
    heading=ParagraphStyle('CNHeading',parent=body,fontSize=14,leading=22,spaceBefore=16,textColor=colors.HexColor('#454fb1'))
    title=ParagraphStyle('CNTitle',parent=heading,fontSize=23,leading=32)
    def p(text,style=body): return Paragraph(escape(str(text)).replace('\n','<br/>'),style)
    story=[p('产品与运营工作报告',title),p(f"REP-{report.pk} · {s['start']} 至 {s['end']}"),p('项目：'+'、'.join(s['projects'])),p(s['scope_note']),p(f"已核对运营记录 {len(s['data'])} 条；待核对 / 差异 {s['pending_data']} 条。"),Spacer(1,12)]
    for label,rows in report_sections(s):
        story.append(p(label,heading))
        if not rows: story.append(p('本期无记录'))
        for name,detail in rows: story.extend([p(name),p(detail),Spacer(1,5)])
    if s.get('include_materials'):
        story.append(p('截图附录与材料索引',heading))
        for item in s['attachments']:
            story.append(p(f"{item['requirement']} · {item['stage']} · 第 {item['round']} 轮 · {item['name']}"))
            attachment=Attachment.objects.filter(pk=item['id']).first()
            if item['kind']=='screenshot' and attachment:
                try:
                    from PIL import Image as PILImage
                    with attachment.file.open('rb') as stream:
                        im=PILImage.open(stream); im.load(); im.thumbnail((1800,2200)); buffer=io.BytesIO(); im.convert('RGB').save(buffer,format='PNG'); buffer.seek(0)
                    w,h=im.size; scale=min(480/w,600/h,1); story.append(Image(buffer,width=w*scale,height=h*scale)); story.append(Spacer(1,10))
                except (OSError,ValueError): story.append(p('原图不可读取，请核查附件存储。'))
    def footer(c,doc):
        c.setFont('NotoSansSC',9); c.setFillColor(colors.HexColor('#8b90a0')); c.drawString(42,25,'序程 · 工作记录与追溯'); c.drawRightString(550,25,str(doc.page))
    SimpleDocTemplate(out,pagesize=(595,842),leftMargin=42,rightMargin=42,topMargin=40,bottomMargin=45).build(story,onFirstPage=footer,onLaterPages=footer)
    return out.getvalue()

def safe_cell(value):
    text=display(value)
    return "'"+text if text.lstrip().startswith(('=','+','-','@')) else text

def export_csv(report):
    out=io.StringIO(); writer=csv.writer(out); writer.writerow(['模块','名称','详细记录'])
    for label,rows in report_sections(report.snapshot):
        for name,detail in rows: writer.writerow([safe_cell(label),safe_cell(name),safe_cell(detail)])
    return ('\ufeff'+out.getvalue()).encode('utf-8')

def export_materials(report):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('report.pdf',export_pdf(report)); z.writestr('report.csv',export_csv(report))
        z.writestr('snapshot.json',json.dumps(report.snapshot,ensure_ascii=False,indent=2))
        missing=[]
        for item in report.snapshot.get('attachments',[]):
            attachment=Attachment.objects.filter(pk=item['id']).first()
            try:
                if not attachment: raise FileNotFoundError
                with attachment.file.open('rb') as f: z.writestr(f"materials/{item['id']}-{Path(item['name']).name}",f.read())
            except (OSError,ValueError): missing.append(item['name'])
        if missing: z.writestr('missing-materials.txt','以下原件无法读取：\n'+'\n'.join(missing))
    return out.getvalue()


def export_xlsx(report):
    # This is application-side export code; no desktop runtime dependencies on the server.
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
    workbook=Workbook(); workbook.remove(workbook.active)
    for label,rows in report_sections(report.snapshot):
        sheet=workbook.create_sheet(label[:31]); sheet.append(['名称','详细记录'])
        for name,detail in rows: sheet.append([safe_cell(name),safe_cell(detail)])
        sheet.freeze_panes='A2'; sheet.auto_filter.ref=sheet.dimensions
        sheet.column_dimensions['A'].width=40; sheet.column_dimensions['B'].width=100
        for cell in sheet[1]: cell.font=Font(bold=True,color='FFFFFF'); cell.fill=PatternFill('solid',fgColor='4358BC')
        for row in sheet.iter_rows(min_row=2):
            for cell in row: cell.alignment=Alignment(wrap_text=True,vertical='top')
            sheet.row_dimensions[row[0].row].height=75
    sources=workbook.create_sheet('范围与来源'); sources.append(['项目','说明'])
    sources.append(['统计周期',f'{report.start} 至 {report.end}']); sources.append(['生成时间',report.snapshot['generated_at']]); sources.append(['口径',report.snapshot['scope_note']])
    sources.column_dimensions['A'].width=20; sources.column_dimensions['B'].width=100
    out=io.BytesIO(); workbook.save(out); return out.getvalue()
