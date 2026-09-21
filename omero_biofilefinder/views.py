#!/usr/bin/env python
#
# Copyright (c) 2024 University of Dundee.
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

import csv
import io
import json
import os
import urllib
from collections import defaultdict

import omero

# TODO: try/except for pyarrow import
import pyarrow as pa
import pyarrow.parquet as pq
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import NoReverseMatch, reverse
from omero.gateway import FileAnnotationWrapper
from omeroweb.decorators import login_required
from omeroweb.webclient.tree import marshal_annotations
from omeroweb.webgateway.views import perform_table_query
from pyarrow import csv as pa_csv

from . import biofilefinder_settings as settings
from .utils import get_image_count

BFF_NAMESPACE = "omero_biofilefinder.parquet"
TABLE_NAMESPACE = "openmicroscopy.org/omero/bulk_annotations"

SCRIPT_PATH = "/omero/annotation_scripts/Export_to_Biofile_Finder.py"

# These are column names that are used for "Open with" in BFF.
# E.g. "Open with > OMERO viewer" and "Open with > OMERO webclient"
VIEWER_LINK = "OMERO viewer"
WEBCLIENT_LINK = "OMERO webclient"


@login_required()
def index(request, conn=None, **kwargs):
    # Placeholder index page
    return render(
        request, "omero_biofilefinder/index.html", {"is_admin": conn.isAdmin()}
    )


def column_description(request, conn=None, **kwargs):
    """
    Return a CSV file with the column descriptions for the BFF app.
    """
    col_desc = [
        ["Column Name", "Description", "Type"],
        [VIEWER_LINK, "Open in OMERO viewer", "Open file link"],
        [WEBCLIENT_LINK, "Open in OMERO webclient", "Open file link"],
    ]
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="Column_Description.csv"'
    writer = csv.writer(response)
    writer.writerows(col_desc)
    return response


def get_bff_url(request, data_url, fname, ext="csv"):
    """
    We build config into query params for the BFF app
    """
    data_url = request.build_absolute_uri(data_url)
    col_desc_url = request.build_absolute_uri(reverse("bff_column_description"))
    # Django may not know it's under https
    if settings.FORCE_HTTPS:
        data_url = data_url.replace("http://", "https://")
        col_desc_url = col_desc_url.replace("http://", "https://")
    source = {
        "uri": data_url,
        "type": ext,
        "name": fname,
    }
    meta = {"name": "Column_Description.csv", "type": "csv", "uri": col_desc_url}
    meta_s = urllib.parse.quote(json.dumps(meta))
    s = urllib.parse.quote(json.dumps(source))
    bff_static = reverse("bff_static", kwargs={"url": ""})
    bff_url = f"{bff_static}?source={s}&sourceMetadata={meta_s}"
    return bff_url


@login_required()
def open_with_bff(request, conn=None, **kwargs):
    """
    Open-with > BFF goes here...

    We give various options to the user to open with BFF.

    1. We generate a URL for loading csv for the project (on the fly)
    and then add that to a BFF url so it loads KVPs on the fly.
    2. If there is a BFF parquet file already attached to the project,
    we can use that instead of the csv file.
    """

    for obj_type in ["project", "plate", "dataset", "image"]:
        obj_id = request.GET.get(obj_type)
        if obj_id is not None:
            break

    if obj_id is None:
        raise Http404("Use ?project=1 or ?dataset=1 or ?plate=1 or ?image=1")
    else:
        obj_id = int(obj_id)

    bff_url = None
    if obj_type in ["project", "plate", "dataset"]:
        csv_url = reverse(
            "omero_biofilefinder_csv", kwargs={"obj_id": obj_id, "obj_type": obj_type}
        )
        bff_url = get_bff_url(request, csv_url, "omero.csv", ext="csv")

    obj = conn.getObject(obj_type, obj_id)
    if obj is None:
        raise Http404(f"{obj_type}:{obj_id} Not Found")

    # image count
    img_count = get_image_count(conn, obj_type.capitalize(), obj_id)

    # If there is a parquet file already attached to the project, we can
    # use that instead of the csv file.
    bff_parquet_anns = []
    table_anns = []
    for ann in obj.listAnnotations(ns=BFF_NAMESPACE):
        if ann.getFile() is not None:
            pq_url = reverse("omero_biofilefinder_fileann", kwargs={"annId": ann.id})
            bff_parquet_anns.append(
                {
                    "id": ann.id,
                    "name": ann.getFile().getName(),
                    "description": ann.getDescription(),
                    "size": ann.getFile().getSize(),
                    "created": ann.creationEventDate().strftime("%Y-%m-%d %H:%M:%S.%Z"),
                    "bff_url": get_bff_url(
                        request, pq_url, "omero.parquet", ext="parquet"
                    ),
                }
            )
    # Same logic as listing OMERO.tables in webclient...
    anns = [
        ann for ann in obj.listAnnotations() if isinstance(ann, FileAnnotationWrapper)
    ]

    def is_table_ann(ann):
        if ann.getNs() == TABLE_NAMESPACE:
            return True
        file = ann.getFile()
        if file is not None and file.getMimetype() == "OMERO.tables":
            return True
        if file is not None and file.getName().endswith(".csv"):
            return True
        return False

    anns = [ann for ann in anns if is_table_ann(ann)]
    anns = sorted(anns, key=lambda x: x.creationEventDate(), reverse=True)

    for ann in anns:
        # Handle csv or OMERO.table (to parquet) links...
        file_name = ann.getFile().getName()
        if ann.getFile().getName().endswith(".csv"):
            url_name = "omero_biofilefinder_csv_to_bff_csv"
            ext = "csv"
        else:
            url_name = "omero_biofilefinder_table_to_parquet"
            file_name = (
                file_name if file_name.endswith(".parquet") else file_name + ".parquet"
            )
            ext = "parquet"
        table_url = reverse(url_name, kwargs={"ann_id": ann.id})
        table_anns.append(
            {
                "id": ann.id,
                "file_id": ann.getFile().id,
                "name": ann.getFile().getName(),
                "description": ann.getDescription(),
                "size": ann.getFile().getSize(),
                "created": ann.creationEventDate().strftime("%Y-%m-%d %H:%M:%S.%Z"),
                "bff_url": get_bff_url(request, table_url, file_name, ext=ext),
                "ext": ext,
            }
        )

    script_service = conn.getScriptService()
    script_id = script_service.getScriptID(SCRIPT_PATH)

    context = {
        "bff_url": bff_url,
        "target": {"dtype": obj_type, "id": obj_id, "name": obj.getName()},
        "bff_parquet_anns": bff_parquet_anns,
        "table_anns": table_anns,
        "script_id": script_id,
        "is_admin": conn.isAdmin(),
        "img_count": img_count,
    }

    return render(request, "omero_biofilefinder/open_with_bff.html", context)


def get_urls(obj_type, obj_id):
    """
    Given a row from a table, find the shape or ROI or image id and return the
    OMERO.web URL and thumbnail URL.
    """
    base_url = reverse("webindex")
    try:
        omero_iviewer_url = reverse("omero_iviewer_index")
    except NoReverseMatch:
        # iviewer not installed
        omero_iviewer_url = None

    thumb_url = ""
    viewer_url = ""
    webclient_url = base_url + f"?show={obj_type}-{obj_id}"

    try:
        if obj_type == "image":
            thumb_url = reverse("webgateway_render_thumbnail", kwargs={"iid": obj_id})
            viewer_url = base_url + f"img_detail/{obj_id}/"
        elif obj_type == "shape":
            thumb_url = reverse(
                "webgateway_render_shape_thumbnail", kwargs={"shapeId": obj_id}
            )
            if omero_iviewer_url:
                viewer_url = omero_iviewer_url + f"?shape={obj_id}"
            else:
                viewer_url = webclient_url
        elif obj_type == "roi":
            thumb_url = reverse(
                "webgateway_render_roi_thumbnail", kwargs={"roiId": obj_id}
            )
            if omero_iviewer_url:
                viewer_url = omero_iviewer_url + f"?roi={obj_id}"
            else:
                viewer_url = webclient_url
    except NoReverseMatch:
        # e.g. column value not an integer - Ignore
        pass

    return {"webclient": webclient_url, "thumbnail": thumb_url, "viewer": viewer_url}


@login_required()
def csv_metadata(request, fileId, conn=None, **kwargs):
    """
    Return metadata for a CSV file as JSON. This is used by BFF to display metadata
    about the file.

    Returns {"columns": [{"name": "Column Name", "type": "string|int|float"}, ...], }
    """
    orig_file = conn.getObject("OriginalFile", fileId)
    if orig_file is None:
        return JsonResponse({"error": "File not found"}, status=404)

    columns = []
    num_rows = 0
    with orig_file.asFileObj() as file_obj:  # Returns a file-like object
        # use csv reader to read first 10 lines to get column names and types
        csv_bytes = file_obj.read()
        arrow_table = pa_csv.read_csv(io.BytesIO(csv_bytes))

        sch = arrow_table.schema
        columns = [
            {"name": name, "type": str(t)} for name, t in zip(sch.names, sch.types)
        ]
        num_rows = arrow_table.num_rows

    metadata = {
        "name": orig_file.getName(),
        "columns": columns,
        "totalCount": num_rows,
    }
    return JsonResponse(metadata)


def get_obj_type_and_column(col_names, first_row=None):
    obj_type = None
    obj_column_idx = None
    for otype in ("shape", "roi", "image"):
        for idx, col in enumerate(col_names):
            # check for "shape", "shape_id", "shape id" etc.
            if col.lower() in (otype, f"{otype}_id", f"{otype} id"):
                if first_row is not None:
                    try:
                        int(first_row[idx])
                        obj_type = otype
                        obj_column_idx = idx
                        break
                    except ValueError:
                        continue
                else:
                    obj_type = otype
                    obj_column_idx = idx
                    break
        if obj_type is not None:
            break
    return obj_type, obj_column_idx


@login_required()
def csv_to_bff_csv(request, ann_id, conn=None, **kwargs):
    """
    Load a CSV FileAnnotation, convert it to the format BFF expects, and return as
    a CSV file for BFF to load. If there is an Image or image column, add columns
    with the OMERO.web image and thumbnail URLs.
    """

    # Get the FileAnnotation
    ann = conn.getObject("FileAnnotation", ann_id)
    if ann is None or ann.getFile() is None:
        return HttpResponse("FileAnnotation not found", status=404)

    orig_file = ann.getFile()
    csv_text = ""
    with orig_file.asFileObj() as file_obj:  # Returns a file-like object
        csv_text = file_obj.read().decode("utf-8")  # Assuming the CSV is utf-8 encoded

    # write a modified csv file to buffer and return as response
    with io.StringIO() as csvfile:
        writer = csv.writer(csvfile)

        # Read the file as text
        # OMERO file_obj is binary, decode as utf-8
        reader = csv.reader(io.StringIO(csv_text))
        try:
            header = next(reader)
            first_row = next(reader)
            print("first row:", first_row)
        except StopIteration:
            return  # Empty file

        # Find key column (case-insensitive)
        obj_type, obj_column_idx = get_obj_type_and_column(header, first_row)
        # Compose new header
        new_header = list(header)
        if obj_column_idx is not None:
            new_header.insert(obj_column_idx + 1, "File Path")
            new_header.insert(obj_column_idx + 2, "Thumbnail")
            new_header.insert(obj_column_idx + 3, VIEWER_LINK)
        if obj_type == "image":
            new_header.insert(obj_column_idx + 4, WEBCLIENT_LINK)

        writer.writerow(new_header)

        # For each row, add OMERO.web URLs if possible
        def handle_row(row):
            new_row = list(row)
            if obj_column_idx is not None:
                urls = get_urls(obj_type, new_row[obj_column_idx])
                # 'File Path', 'Thumbnail', and 'Viewer Link' columns
                new_row.insert(obj_column_idx + 1, urls["webclient"])
                new_row.insert(obj_column_idx + 2, urls["thumbnail"])
                new_row.insert(obj_column_idx + 3, urls["viewer"])
            if obj_type == "image":
                # WEBCLIENT_LINK is only relevant for images
                new_row.insert(obj_column_idx + 4, urls["webclient"])
            writer.writerow(new_row)

        # write the first_row, then all remaining rows
        handle_row(first_row)
        for row in reader:
            handle_row(row)

        response = HttpResponse(
            csvfile.getvalue(),
            content_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{orig_file.getName()}"'
            },
        )
        return response


@login_required()
def omero_to_csv(request, obj_type, obj_id, conn=None, **kwargs):
    """
    Convert KVPs to a csv file on the fly. This is used to load KVPs into
    BFF without needing to generate a file first.
    """

    obj = conn.getObject(obj_type, obj_id)
    if obj is None:
        raise Http404("{obj_type}:{obj_id} Not Found")

    image_ids = []
    parent_names_by_iid = {}
    parent_colname = "Dataset"

    if obj_type == "project" or obj_type == "dataset":
        if obj_type == "project":
            datasets = list(obj.listChildren())
        else:
            datasets = [obj]
        for dataset in datasets:
            for image in dataset.listChildren():
                image_ids.append(image.id)
                parent_names_by_iid[image.id] = dataset.getName()
    elif obj_type == "plate":
        parent_colname = "Well"
        for well in obj.listChildren():
            for ws in well.listChildren():
                image = ws.getImage()
                image_ids.append(image.id)
                parent_names_by_iid[image.id] = well.getWellPos()

    # We use page=-1 to avoid pagination (default is 500)
    anns, experimenters = marshal_annotations(
        conn, image_ids=image_ids, ann_type="map", page=-1
    )

    # Get all the Keys...
    keys = set()
    for ann in anns:
        for key_val in ann["values"]:
            keys.add(key_val[0])

    # Add values to dict {image_id: {key: [list, of, values]}}
    kvp = {}
    for ann in anns:
        image_id = ann["link"]["parent"]["id"]
        if image_id not in kvp:
            kvp[image_id] = defaultdict(list)
        for key_val in ann["values"]:
            key = key_val[0]
            value = key_val[1]
            kvp[image_id][key].append(value)

    column_names = [
        "File Path",
        "File Name",
        WEBCLIENT_LINK,
        VIEWER_LINK,
        parent_colname,
        "Thumbnail",
    ]
    column_names.extend(list(keys))
    column_names.append("Uploaded")

    # write csv to return as http response
    with io.StringIO() as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(column_names)
        for image_id in image_ids:
            urls = get_urls("image", image_id)
            values = kvp.get(image_id, {})
            image = conn.getObject("Image", image_id)
            row = [
                urls["webclient"],
                image.getName() if image else "Not Found",
                urls["webclient"],
                urls["viewer"],
                parent_names_by_iid.get(image_id, "Not Found"),
                urls["thumbnail"],
            ]
            for key in keys:
                row.append(",".join(values.get(key, [])))
            row.append(image.creationEventDate().strftime("%Y-%m-%d %H:%M:%S.%Z"))
            writer.writerow(row)

        response = HttpResponse(csvfile.getvalue(), content_type="text/csv")
        return response


@login_required()
def table_to_parquet(request, ann_id, conn=None, **kwargs):
    """
    Convert an OMERO.table to a parquet file on the fly.
    """
    # If BFF is trying to load a 0 byte file, we return an empty response
    if request.headers.get("Range") == "bytes=0-0":
        return HttpResponse("", status=200)

    ann = conn.getObject("Annotation", ann_id)
    if ann is None:
        return HttpResponse("Annotation not found", status=404)
    fileid = ann.getFile().id if ann.getFile() else None
    if fileid is None:
        return HttpResponse("Annotation does not have a file", status=400)

    query = request.GET.get("query", "*")
    col_names = request.GET.getlist("col_names")

    limit = 10000
    offset = 0
    row_count = None

    pyarrow_tables = []

    # e.g. rows link to "shape" or "roi" or "image"
    obj_type = None
    obj_column_idx = None

    while row_count is None or offset < row_count:
        table_data = perform_table_query(
            conn, fileid, query, col_names, offset=offset, limit=limit
        )

        rows = table_data["data"]["rows"]

        if offset == 0:
            row_count = table_data["meta"]["totalCount"]
            columns = table_data["data"]["columns"]
            obj_type, obj_column_idx = get_obj_type_and_column(columns, rows[0])
            if obj_type is None or obj_column_idx is None:
                return HttpResponse(
                    "No image, roi or shape columns in table", status=400
                )
            # Add a column for file paths and thumbnails
            cols_to_add = ["File Path", VIEWER_LINK]
            if obj_type == "image":
                cols_to_add.append(WEBCLIENT_LINK)
            column_names = cols_to_add + columns + ["Thumbnail"]

        file_paths = []
        thumbnail_urls = []
        viewer_urls = []

        for row in rows:
            urls = get_urls(obj_type, row[obj_column_idx])
            file_paths.append(urls["webclient"])
            thumbnail_urls.append(urls["thumbnail"])
            viewer_urls.append(urls["viewer"])

        column_data = [file_paths, viewer_urls]
        if obj_type == "image":
            # open with webclient uses File Path url
            column_data.append(file_paths)
        for col in range(len(columns)):
            col_data = [row[col] for row in rows]
            column_data.append(col_data)
        column_data.append(thumbnail_urls)
        pyarrow_tables.append(pa.table(column_data, names=column_names))

        offset += limit

    combined_table = pa.concat_tables(pyarrow_tables, promote_options="default")
    combined_table.combine_chunks()

    with io.BytesIO() as buffer:
        pq.write_table(combined_table, buffer)
        ct = "application/vnd.apache.parquet"
        response = HttpResponse(buffer.getvalue(), content_type=ct)
        response["Content-Disposition"] = (
            f'attachment; filename="omero_table_{fileid}.parquet"'
        )
        return response


def app(request, url, **kwargs):
    from django.contrib.staticfiles.storage import staticfiles_storage

    if len(url) == 0:
        url = "index.html"

    static_path = staticfiles_storage.path("omero_biofilefinder/dist/" + url)

    mode = "r"
    if url.endswith(".png"):
        mode = "rb"

    with open(static_path, mode=mode) as f:
        content = f.read()

        # We need to replace the basename in the js file
        if url.endswith(".js") and url.startswith("app."):
            # e.g. "/omero_biofilefinder/bff"
            basename = reverse("omero_biofilefinder_index") + "bff"
            content = content.replace('{basename:""}', f'{{basename:"{basename}"}}')

        response = HttpResponse(content)
        if url.endswith(".js"):
            response["Content-Type"] = "application/javascript"
        elif url.endswith(".css"):
            response["Content-Type"] = "text/css"
        elif url.endswith(".png"):
            response["Content-Type"] = "image/png"
        elif url.endswith(".html"):
            response["Content-Type"] = "text/html"
    return response


@login_required(isAdmin=True)
def upload_omero_script(request, conn=None, **kwargs):
    """Uploads or Replaces the Export_to_Biofile_Finder.py"""

    if not request.method == "POST":
        return HttpResponse("Need to use POST")

    script_service = conn.getScriptService()
    script_id = script_service.getScriptID(SCRIPT_PATH)

    this_dir = os.path.dirname(os.path.abspath(__file__))
    script_path = os.path.join(this_dir, "scripts", SCRIPT_PATH[1:])

    try:
        with open(script_path) as script_file:
            script_text = script_file.read()
    except FileNotFoundError:
        return JsonResponse({"Error": "Failed to load script from " + script_path})

    # If script exists, replace. Otherwise upload
    try:
        if script_id > 0:
            orig_file = omero.model.OriginalFileI(script_id, False)
            script_service.editScript(orig_file, script_text)
            message = "Script Replaced"
        else:
            script_id = script_service.uploadOfficialScript(SCRIPT_PATH, script_text)
            message = "Script Uploaded"
    except omero.ValidationException as ex:
        return JsonResponse({"Error": ex.message})

    return JsonResponse({"Message": message, "script_id": script_id})


@login_required(isAdmin=True)
def admin_page(request, conn=None, **kwargs):
    """Admin page to upload or replace the OMERO.script"""

    script_service = conn.getScriptService()
    script_id = script_service.getScriptID(SCRIPT_PATH)

    print("Script ID:", script_id)
    context = {
        "script_id": script_id,
    }
    return render(request, "omero_biofilefinder/admin.html", context)
