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
import urllib
from collections import defaultdict

import omero

# TODO: try/except for pyarrow import
import pyarrow as pa
import pyarrow.parquet as pq
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from omero import ValidationException
from omero.rtypes import rstring
from omeroweb.decorators import login_required
from omeroweb.webclient.tree import marshal_annotations
from omeroweb.webgateway.views import perform_table_query

from . import biofilefinder_settings as settings

BFF_NAMESPACE = "omero_biofilefinder.parquet"
TABLE_NAMESPACE = "openmicroscopy.org/omero/bulk_annotations"


@login_required()
def index(request, conn=None, **kwargs):
    # Placeholder index page
    return render(request, "omero_biofilefinder/index.html", {})


def get_bff_url(request, data_url, fname, ext="csv"):
    """
    We build config into query params for the BFF app
    """
    data_url = request.build_absolute_uri(data_url)
    # Django may not know it's under https
    if settings.FORCE_HTTPS:
        data_url = data_url.replace("http://", "https://")
    source = {
        "uri": data_url,
        "type": ext,
        "name": fname,
    }
    s = urllib.parse.quote(json.dumps(source))
    bff_static = reverse("bff_static", kwargs={"url": ""})
    bff_url = f"{bff_static}?source={s}"
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

    for obj_type in ["project", "plate", "dataset"]:
        obj_id = request.GET.get(obj_type)
        if obj_id is not None:
            break

    if obj_id is None:
        raise Http404("Use ?project=1 or ?screen=1")
    else:
        obj_id = int(obj_id)

    # csv_url = reverse(
    #     "omero_biofilefinder_csv", kwargs={"obj_id": obj_id, "obj_type": obj_type}
    # )
    # bff_url = get_bff_url(request, csv_url, "omero.csv", ext="csv")

    bff_url = reverse("omero_biofilefinder_app") + f"?{obj_type}={obj_id}"

    # We want to pick some columns to show in the BFF app.
    # Need to know a few Keys from Key-Value pairs.
    # Let's just check first 5 images...
    image_ids = []
    obj = conn.getObject(obj_type, obj_id)
    if obj is None:
        raise Http404("{obj_type}:{obj_id} Not Found")

    if obj_type == "project" or obj_type == "dataset":
        if obj_type == "project":
            datasets = list(obj.listChildren())
        else:
            datasets = [obj]
        for dataset in datasets:
            for image in dataset.listChildren():
                image_ids.append(image.id)
                if len(image_ids) > 5:
                    break
            if len(image_ids) > 5:
                break
    elif obj_type == "plate":
        for well in obj.listChildren():
            image = well.getImage(0)
            image_ids.append(image.id)
            if len(image_ids) > 5:
                break

    if len(image_ids) == 0:
        return HttpResponse(f"No images found in {obj_type}:{obj_id}")

    # Get KVP keys for 5 images...
    # anns, experimenters = marshal_annotations(conn, image_ids=image_ids,
    # ann_type="map")
    # keys = defaultdict(int)
    # for ann in anns:
    #     for key, val in ann["values"]:
    #         keys[key] += 1
    # # Sort keys by number of occurrences and take the top 3
    # sorted_keys = sorted(keys.keys(), key=lambda x: keys[x], reverse=True)
    # # Show max 5 columns (4 keys)
    # col_names = ["File Name", "Dataset"] + sorted_keys[:3]
    # col_width = 1 / len(col_names)
    # # column query e.g. "File Name:0.25,Dataset:0.25,Key1:0.25,Key2:0.25"
    # col_query = ",".join([f"{name}:{col_width}:.2f" for name in col_names])
    # bff_url += "&c=" + col_query

    # If there is a parquet file already attached to the project, we can
    # use that instead of the csv file.
    bff_parquet_anns = []
    table_anns = []
    for ann in obj.listAnnotations(ns=BFF_NAMESPACE):
        if ann.getFile() is not None:
            pq_url = reverse("omero_biofilefinder_fileann", kwargs={"ann_id": ann.id})
            bff_parquet_anns.append(
                {
                    "id": ann.id,
                    "name": ann.getFile().getName(),
                    "description": ann.getDescription(),
                    "size": ann.getFile().getSize(),
                    "created": ann.creationEventDate().strftime("%Y-%m-%d %H:%M:%S.%Z"),
                    "bbf_url": get_bff_url(
                        request, pq_url, "omero.parquet", ext="parquet"
                    ),
                }
            )
    for ann in obj.listAnnotations(ns=TABLE_NAMESPACE):
        table_pq_url = reverse(
            "omero_biofilefinder_table_to_parquet", kwargs={"ann_id": ann.id}
        )
        table_anns.append(
            {
                "id": ann.id,
                "name": ann.getFile().getName(),
                "description": ann.getDescription(),
                "size": ann.getFile().getSize(),
                "created": ann.creationEventDate().strftime("%Y-%m-%d %H:%M:%S.%Z"),
                "bff_url": get_bff_url(
                    request, table_pq_url, "omero_table.parquet", ext="parquet"
                ),
            }
        )

    context = {
        "bff_url": bff_url,
        "target": {"dtype": obj_type, "id": obj_id, "name": obj.getName()},
        "bff_parquet_anns": bff_parquet_anns,
        "table_anns": table_anns,
    }

    return render(request, "omero_biofilefinder/open_with_bff.html", context)


@login_required()
def omero_to_csv(request, obj_type, obj_id, conn=None, **kwargs):

    obj = conn.getObject(obj_type, obj_id)
    if obj is None:
        raise Http404("{obj_type}:{obj_id} Not Found")

    image_ids = []
    roi_ids = []
    shape_ids = []
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
    elif obj_type == "image":
        # Load ROIs...
        roi_service = conn.getRoiService()
        results = roi_service.findByImage(obj.id, None)
        for r in results.rois:
            parent_names_by_iid[obj.id] = f"ROI:{obj.id}"  # TODO: check text value
            # for now, just get first shape ID for each ROI
            shp_ids = [s.id.val for s in r.copyShapes() if s.id is not None]
            if len(shp_ids) > 0:
                roi_ids.append(r.id.val)
                shape_ids.append(shp_ids[0])

    # We use page=-1 to avoid pagination (default is 500)
    obj_ids = image_ids if len(image_ids) > 0 else roi_ids
    print("Obj IDs:", obj_ids)
    print("shape IDs:", shape_ids)
    dtype = "Image" if len(image_ids) > 0 else "Roi"
    if len(image_ids) > 0:
        anns, experimenters = marshal_annotations(
            conn, image_ids=image_ids, ann_type="map", page=-1
        )
    elif len(roi_ids) > 0:
        anns, experimenters = marshal_annotations(
            conn, roi_ids=roi_ids, ann_type="map", page=-1
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

    column_names = ["File Path", f"{dtype} ID", "File Name", "Thumbnail"]
    if dtype == "Roi":
        column_names.append("Shape ID")
    else:
        column_names.append(parent_colname)
    column_names.extend(list(keys))
    column_names.append("Uploaded")

    # write csv to return as http response
    with io.StringIO() as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(column_names)
        for row_idx, obj_id in enumerate(obj_ids):
            values = kvp.get(obj_id, {})
            thumb_url = ""
            obj_name = ""
            object_url = request.build_absolute_uri(reverse("webindex"))
            obj = conn.getObject(dtype, obj_id)
            print("obj", obj, dtype, obj_id)
            if dtype == "Roi":
                shape_id = shape_ids[row_idx]
                thumb_url = reverse(
                    "webgateway_render_shape_thumbnail", kwargs={"shapeId": shape_id}
                )
                object_url += f"?show=roi-{obj_id}&_=.png"
                obj_name = f"ROI:{obj_id}"
            else:
                thumb_url = reverse(
                    "webgateway_render_thumbnail", kwargs={"iid": obj_id}
                )
                obj_name = (obj.getName() if obj else "Not Found",)
                # we end url with .png so that BFF enables open-with "Browser"
                object_url += f"?show=obj-{obj_id}&_=.png"

            row = [
                object_url,
                obj_id,
                obj_name,
                request.build_absolute_uri(thumb_url),
            ]
            if dtype == "Roi":
                row.append(shape_id)
            else:
                row.append(parent_names_by_iid.get(obj_id, "Not Found"))

            for key in keys:
                row.append(",".join(values.get(key, [])))
            row.append(obj.creationEventDate().strftime("%Y-%m-%d %H:%M:%S.%Z"))
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

    # NB: we don't need absolute URLs here, as the BFF app is hosted
    # by omero-web. If we want to use BFF outside of omero-web,
    # we would need to change the URLs to absolute URLs.
    base_url = reverse("index")
    # we end URL with .png so that BFF enables open-with "Browser"
    web_url = f"{base_url}webclient/?show=image-"
    thumb_url = f"{base_url}webgateway/render_thumbnail/"

    limit = 10000
    offset = 0
    row_count = None

    pyarrow_tables = []

    while row_count is None or offset < row_count:
        table_data = perform_table_query(
            conn, fileid, query, col_names, offset=offset, limit=limit
        )

        if offset == 0:
            row_count = table_data["meta"]["totalCount"]
            columns = table_data["data"]["columns"]
            image_col = -1
            image_col = (
                columns.index("Image") if "Image" in columns else columns.index("image")
            )
            if image_col == -1:
                return HttpResponse("No Image or image column in table", status=400)
            # Add a column for file paths and thumbnails
            column_names = ["File Path"] + columns + ["Thumbnail"]

        rows = table_data["data"]["rows"]
        file_paths = [f"{web_url}{row[image_col]}&_=.png" for row in rows]
        column_data = [file_paths]
        for col in range(len(columns)):
            col_data = [row[col] for row in rows]
            column_data.append(col_data)
        thumbnail_urls = [f"{thumb_url}{row[image_col]}/" for row in rows]
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


@login_required()
def app_page(request, conn=None, **kwargs):

    # Handle e.g. ?dataset=1 - iframe app loads KV pairs on the fly
    # or e.g. ?file=2 - load file annotation: OMERO.table -> parquet
    # or load parquet file directly

    bff_url = None
    group_id = None
    for obj_type in ["project", "plate", "dataset", "image"]:
        obj_id = request.GET.get(obj_type)
        if obj_id is not None:
            obj = conn.getObject(obj_type, int(obj_id))
            if obj is None:
                raise Http404(f"{obj_type}:{obj_id} Not Found")
            group_id = obj.getDetails().group.id.val
            csv_url = reverse(
                "omero_biofilefinder_csv",
                kwargs={"obj_id": obj_id, "obj_type": obj_type},
            )
            bff_url = get_bff_url(request, csv_url, "omero.csv", ext="csv")
            break

    context = {
        "bff_url": bff_url,
        "group_id": group_id,
    }
    return render(request, "omero_biofilefinder/app_page.html", context)


@login_required()
def handle_annotation(request, conn=None, **kwargs):
    update_service = conn.getUpdateService()

    # Handle annotation submission
    if request.method == "POST":
        form_data = request.POST

        object_ids = [int(iid) for iid in form_data.get("objectIds", "").split(",")]
        if len(object_ids) == 0:
            return JsonResponse(
                {"status": "error", "message": "No objectIds"}, status=400
            )

        obj_type = request.POST.get("objectType", "Image")
        print("Object IDs:", object_ids)
        # Get group from first object
        obj = conn.getObject(obj_type, int(object_ids[0]))
        if obj is None:
            raise Http404(f"{obj_type}:{object_ids[0]} Not Found")
        group_id = obj.getDetails().group.id.val
        conn.SERVICE_OPTS.setOmeroGroup(group_id)

        new_tag = form_data.get("new_tag", "")
        print("New Tag:", new_tag)

        annotations = []

        maps_text = form_data.get("maps", "")
        if maps_text:
            kvps = []
            for line in maps_text.splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    kvps.append([key.strip(), value.strip()])
            if len(kvps) > 0:
                ann = omero.gateway.MapAnnotationWrapper(conn)
                ann.setValue(kvps)
                ann.setNs(rstring("omero.biofilefinder.maps"))
                ann.save()
                print("Created MapAnnotation:", ann.id)
                annotations.append(omero.model.MapAnnotationI(ann.id, False))

        if new_tag:
            tag = omero.model.TagAnnotationI()
            tag.textValue = rstring(new_tag)
            tag = update_service.saveAndReturnObject(tag)
            print("Created Tag:", tag.id)
            annotations.append(tag)

        tags = form_data.getlist("tag", [])
        for tag_id in tags:
            tag = omero.model.TagAnnotationI(tag_id, False)
            annotations.append(tag)

        # Link existing and new Tags to Images
        for ann in annotations:
            for object_id in object_ids:
                if obj_type == "Image":
                    link = omero.model.ImageAnnotationLinkI()
                    link.parent = omero.model.ImageI(object_id, False)
                else:
                    link = omero.model.RoiAnnotationLinkI()
                    link.parent = omero.model.RoiI(object_id, False)
                link.child = ann

                try:
                    link = update_service.saveAndReturnObject(link)
                    print("Linked Annotation with Link:", link.id.val)
                except ValidationException as e:
                    # E.g. if link aready exists
                    print("Error linking Annotation to Image:", e)
        # Process the form data as needed
        return JsonResponse({"status": "success"})
    return JsonResponse({"status": "error"}, status=400)


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
