from django.contrib import admin
from .models import *
from .services import audit
admin.site.site_header='序程 · 系统管理'
admin.site.site_title='序程管理后台'
admin.site.index_title='账号、角色与项目配置'
class MemberInline(admin.TabularInline):
    model=Membership; extra=1
@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display=['name','owner','archived']; inlines=[MemberInline]
    def has_module_permission(self,request): return request.user.is_superuser
    def has_view_permission(self,request,obj=None): return request.user.is_superuser
    def has_change_permission(self,request,obj=None): return request.user.is_superuser
    def has_add_permission(self,request): return request.user.is_superuser
    def has_delete_permission(self,request,obj=None): return False
    def save_model(self,request,obj,form,change):
        super().save_model(request,obj,form,change); audit(request.user,obj,'修改项目' if change else '创建项目',str(form.changed_data))
    def save_formset(self,request,form,formset,change):
        instances=formset.save(commit=False)
        for obj in formset.deleted_objects:
            audit(request.user,form.instance,'移除项目成员',f'{obj.user} · {obj.role}'); obj.delete()
        for obj in instances:
            obj.save(); audit(request.user,form.instance,'配置项目角色',f'{obj.user} · {obj.role}')
        formset.save_m2m()
class ReadOnlyAdmin(admin.ModelAdmin):
    def has_module_permission(self,request): return request.user.is_superuser
    def has_view_permission(self,request,obj=None): return request.user.is_superuser
    def has_add_permission(self,request): return False
    def has_change_permission(self,request,obj=None): return False
    def has_delete_permission(self,request,obj=None): return False
for model in [Requirement,Record,Bug,BugEvent,Attachment,Audit]: admin.site.register(model,ReadOnlyAdmin)
