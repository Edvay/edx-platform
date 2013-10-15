"""
A Django command that exports a course to a tar.gz file.
"""

import shutil
import tarfile
from tempfile import mkdtemp
from textwrap import dedent

from path import path

from django.core.management.base import BaseCommand, CommandError

from xmodule.modulestore.django import modulestore
from xmodule.contentstore.django import contentstore
from xmodule.modulestore.xml_exporter import export_to_xml


class Command(BaseCommand):
    """
    Export a course to XML. The output is compressed as a tar.gz file

    """
    args = "<course_id> <output_filename>"
    help = dedent(__doc__).strip()

    def handle(self, *args, **options):
        try:
            course_id = args[0]
            filename = args[1]
        except IndexError:
            raise CommandError("Insufficient arguments")

        export_course_to_tarfile(course_id, filename)


def export_course_to_tarfile(course_id, filename):
    """Exports a course into a tar.gz file"""
    tmp_dir = mkdtemp()
    try:
        course_dir = export_course_to_directory(course_id, tmp_dir)
        compress_directory(course_dir, filename)
    finally:
        shutil.rmtree(tmp_dir)


def export_course_to_directory(course_id, root_dir):
    """Export course into a directory"""
    store = modulestore()
    course = store.get_course(course_id)
    if course is None:
        raise CommandError("Invalid course_id")

    course_name = course.location.course_id.replace('/', '-')
    export_to_xml(store, None, course.location, root_dir, course_name)

    course_dir = path(root_dir) / course_name
    return course_dir


def compress_directory(directory, filename):
    """Compress a directrory into a tar.gz file"""
    mode = 'w:gz'
    name = path(directory).name
    with tarfile.open(filename, mode) as tar_file:
        tar_file.add(directory, arcname=name)
