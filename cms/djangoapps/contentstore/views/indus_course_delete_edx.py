from django.http import HttpResponseRedirect,HttpResponse
import json
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from contentstore.utils import delete_course
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import modulestore
def indus_course_delete_edx(request):
	if request.is_ajax():
	    try:
	    	course_key = CourseKey.from_string(request.POST.get('courseid'))
	    except InvalidKeyError:
	    	raise CommandError("Invalid course_key: '%s'." % options['course_key'])
	    delete_course(course_key,request.user.id)
	    print "Deleted course {}".format(course_key)
	    return HttpResponse(json.dumps('data'), content_type="application/json")
    










