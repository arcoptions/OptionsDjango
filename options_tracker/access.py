"""Access-control helpers shared by legacy Django views."""

from functools import wraps

from django.contrib.admin.views.decorators import staff_member_required


SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def staff_required_for_unsafe_methods(view_func):
    """Require a staff session for requests that may change application state."""
    protected_view = staff_member_required(view_func, login_url="admin:login")

    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if request.method in SAFE_METHODS:
            return view_func(request, *args, **kwargs)
        return protected_view(request, *args, **kwargs)

    return wrapped
