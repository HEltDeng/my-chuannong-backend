"""Explicit operator scopes, shared by account validation and campus routes."""

from core import ApiError

PROVIDER_ROLES = {
    'DRIVING_SCHOOL_OPERATOR': 'driving_school',
    'IELTS_TOEFL_OPERATOR': 'ielts_toefl',
    'CIVIL_POSTGRAD_OPERATOR': 'civil_postgrad',
    'GYM_OPERATOR': 'gym',
}
GUIDE_ROLES = {
    'ERRAND_OPERATOR': 'errand',
    'REPAIR_OPERATOR': 'logistics_repair',
    'ELECTRIC_OPERATOR': 'electric',
}
MODULE_ROLES = set(PROVIDER_ROLES) | set(GUIDE_ROLES) | {'SECONDHAND_OPERATOR'}

ANNOUNCEMENT_ROLES = {
    **PROVIDER_ROLES, **GUIDE_ROLES,
    'SECONDHAND_OPERATOR': 'secondhand',
    'LOST_FOUND_OPERATOR': 'lost-found',
    'LIBRARY_OPERATOR': 'resources',
}
ANNOUNCEMENT_MODULES = {'general'} | set(ANNOUNCEMENT_ROLES.values())


def announcement_scope(role, requested=None):
    """Resolve an operator's module before either reading or changing notices."""
    if requested is not None and (not isinstance(requested, str) or requested not in ANNOUNCEMENT_MODULES):
        raise ApiError(422, 'VALIDATION_ERROR', '公告所属板块无效')
    own = ANNOUNCEMENT_ROLES.get(role)
    if own:
        if requested and requested != own:
            raise ApiError(403, 'ADMIN_MODULE_FORBIDDEN', '当前管理员无权访问该板块公告')
        return own
    if role not in {'SUPER_ADMIN', 'CONTENT_OPERATOR'}:
        raise ApiError(403, 'ADMIN_MODULE_FORBIDDEN', '当前管理员无权管理公告')
    return requested
