#!/usr/bin/env python
#
# Copyright (c) 2025 University of Dundee.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

from django.urls import path, re_path
from omeroweb.webclient.views import download_annotation

from . import views

urlpatterns = [
    # index 'home page' of the app
    path("", views.index, name="omero_biofilefinder_index"),
    path(
        "upload_omero_script", views.upload_omero_script, name="bff_upload_omero_script"
    ),
    path("admin", views.admin_page, name="omero_biofilefinder_admin"),
    # entry-point - user chooses how to open BFF ?project=1 or ?dataset=2 etc
    path("open_with_bff", views.open_with_bff, name="omero_biofilefinder_openwith"),
    # when BFF loads a parquet file, the url needs to end with .parquet
    path(
        "fileann/<int:annId>/omero.parquet",
        download_annotation,
        name="omero_biofilefinder_fileann",
    ),
    # Take a regular csv (with an Image or image column) and serve as a BFF-compatible
    # csv (with Thumbnail and File Path columns)
    path(
        "csv/<int:ann_id>/omero.csv",
        views.csv_to_bff_csv,
        name="omero_biofilefinder_csv_to_bff_csv",
    ),
    path(
        "table/<int:ann_id>/omero.parquet",
        views.table_to_parquet,
        name="omero_biofilefinder_table_to_parquet",
    ),
    # equivalent to /webgateway/table/ID/metadata but for CSV file
    path(
        "csv/<int:fileId>/metadata/",
        views.csv_metadata,
        name="omero_biofilefinder_csv_metadata",
    ),
    re_path(
        r"^(?P<obj_type>(project|dataset|plate))/(?P<obj_id>[0-9]+)$",
        views.omero_to_csv,
        name="omero_biofilefinder_csv",
    ),
    re_path(r"^bff/app/(?P<url>.*)$", views.app, name="bff_static"),
]
