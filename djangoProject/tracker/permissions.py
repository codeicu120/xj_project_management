"""Shared action policy for forms, dashboards and mutation services."""
STAGE_ROLES = ['需求提起人', '产品负责人', '需求提起人', '分配负责人', '开发人员', '测试负责人', '需求提起人', '开发人员', '代码审核人', '结项人']


def allowed(user, project):
    return user.is_active and (user.is_superuser or project.owner_id == user.pk or project.memberships.filter(user=user).exists())


def has_role(user, project, roles):
    return user.is_active and (user.is_superuser or project.memberships.filter(user=user, role__in=roles).exists())


def can_process(user, req):
    return allowed(user, req.project) and (user.is_superuser or (req.owner_id == user.pk and (req.stage == '10' or has_role(user, req.project, [STAGE_ROLES[int(req.stage)]]))))


def requirement_actions(user, req):
    if req.project.archived or not can_process(user, req):
        return []
    if req.stage == '10':
        return [('note', '补充记录')]
    actions = [('advance', '确认并进入下一环节')]
    if req.stage != '0':
        actions.append(('return', '退回上游环节'))
    return actions + [('transfer', '转交负责人'), ('note', '补充记录')]


def bug_actions(user, bug):
    project = bug.requirement.project
    if not allowed(user, project) or project.archived:
        return []
    tester = has_role(user, project, ['测试负责人'])
    if bug.status == 'closed':
        return [('fail', '重新打开')] if tester else []
    actions = []
    if bug.status == 'open' and (user.is_superuser or (bug.owner_id == user.pk and has_role(user, project, ['开发人员']))):
        actions.append(('fix', '提交修复'))
    if bug.status == 'verify' and tester:
        actions.extend([('pass', '回归通过并关闭'), ('fail', '回归失败')])
    if bug.status in ['open', 'verify'] and (user.is_superuser or project.owner_id == user.pk or has_role(user, project, ['测试负责人', '分配负责人'])):
        actions.append(('assign', '指派负责人'))
    return actions
