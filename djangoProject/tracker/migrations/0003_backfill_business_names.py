from django.db import migrations

def forward(apps,schema_editor):
    User=apps.get_model('auth','User')
    names={u.pk:((u.first_name+' '+u.last_name).strip() or u.username) for u in User.objects.all()}
    Requirement=apps.get_model('tracker','Requirement')
    for req in Requirement.objects.all().iterator():
        req.business_owner=names.get(req.owner_id,'历史负责人未记录')
        req.proposer=names.get(req.creator_id,'历史提起人未记录')
        req.save(update_fields=['business_owner','proposer'])
    Bug=apps.get_model('tracker','Bug')
    for bug in Bug.objects.all().iterator():
        bug.business_owner=names.get(bug.owner_id,'历史负责人未记录'); bug.save(update_fields=['business_owner'])
    # Old attachments are not automatically treated as verified chat evidence.

class Migration(migrations.Migration):
    dependencies=[('tracker','0002_dataconnection_attachment_kind_attachment_version_and_more')]
    operations=[migrations.RunPython(forward,migrations.RunPython.noop)]
