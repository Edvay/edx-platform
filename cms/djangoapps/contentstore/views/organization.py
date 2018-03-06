"""Organizations views for use with Studio."""
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.utils.decorators import method_decorator
from django.views.generic import View

from openedx.core.djangolib.js_utils import dump_js_escaped_json
from util.organizations_helpers import get_organizations
from lms.djangoapps.create_site.models import EdvayInstance

class OrganizationListView(View):
    """View rendering organization list as json.

    This view renders organization list json which is used in org
    autocomplete while creating new course.
    """

    @method_decorator(login_required)
    def get(self, request, *args, **kwargs):
        """Returns organization list as json."""
        organizations = get_organizations()
        org_names_list = [(org["short_name"]) for org in organizations]
        return HttpResponse(dump_js_escaped_json(org_names_list), content_type='application/json; charset=utf-8')

class AllowedOrganization(View):
    """View rendering organization list as json.

    This view renders organization list json which is used in org
    autocomplete while creating new course.
    """

    @method_decorator(login_required)
    def get(self, request, *args, **kwargs):
        """Returns organization list as json."""   
        edvayInstances = EdvayInstance.objects.get(user=request.user)
        org = edvayInstances.org_name     
        org_names_list = [org]
        return HttpResponse(dump_js_escaped_json(org_names_list), content_type='application/json; charset=utf-8')