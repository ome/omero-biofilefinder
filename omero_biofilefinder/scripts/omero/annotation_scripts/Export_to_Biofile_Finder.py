#!/usr/bin/env python

# -----------------------------------------------------------------------------
#   Copyright (C) 2025 University of Dundee. All rights reserved.


#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 2 of the License, or
#   (at your option) any later version.
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.

#   You should have received a copy of the GNU General Public License along
#   with this program; if not, write to the Free Software Foundation, Inc.,
#   51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

# ------------------------------------------------------------------------------

"""
This script takes a number of images and saves individual image planes in a
zip file for download.
"""

import argparse
import csv
import os
from collections import defaultdict
from datetime import datetime

import omero
import omero.scripts as scripts
import omero.util.script_utils as script_utils
import pyarrow as pa
import pyarrow.parquet as pq
from omero import ClientError
from omero.gateway import BlitzGateway
from omero.rtypes import rlong, robject, rstring
from pyarrow import csv as pa_csv

VERSION = "0.1.0.dev0"
BFF_NAMESPACE = "omero_biofilefinder.parquet"


def marshal_annotations(
    conn,
    project_ids=None,
    dataset_ids=None,
    image_ids=None,
    screen_ids=None,
    plate_ids=None,
    run_ids=None,
    well_ids=None,
    ann_type=None,
    ns=None,
):
    annotations = []
    qs = conn.getQueryService()
    where_clause = ["pa.id in (:ids)"]
    if ann_type == "tag":
        where_clause.append("ch.class=TagAnnotation")
    elif ann_type == "map":
        where_clause.append("ch.class=MapAnnotation")
    if ns is not None:
        where_clause.append("ch.ns=:ns")

    dtypes = [
        "Project",
        "Dataset",
        "Image",
        "Screen",
        "Plate",
        "PlateAcquisition",
        "Well",
    ]
    obj_ids = [
        project_ids,
        dataset_ids,
        image_ids,
        screen_ids,
        plate_ids,
        run_ids,
        well_ids,
    ]

    for dtype, ids in zip(dtypes, obj_ids):
        if ids is None or len(ids) == 0:
            continue
        params = omero.sys.ParametersI()
        params.addIds(ids)
        q = """
            select oal from {}AnnotationLink as oal
            join fetch oal.details.creationEvent
            join fetch oal.details.owner
            left outer join fetch oal.child as ch
            left outer join fetch oal.parent as pa
            join fetch ch.details.creationEvent
            join fetch ch.details.owner {}
            left outer join fetch ch.file as file
            where {} order by ch.ns
            """.format(
            dtype,
            "join fetch pa.plate" if dtype == "Well" else "",
            " and ".join(where_clause),
        )

        for link in qs.findAllByQuery(q, params, conn.SERVICE_OPTS):
            annotation = link.child
            ann_class = annotation.__class__.__name__

            ann = {}
            ann["class"] = ann_class
            ann["id"] = annotation.id.val
            if ann_class == "MapAnnotationI":
                kvs = [[kv.name, kv.value] for kv in annotation.getMapValue()]
                ann["values"] = kvs
            ann["link"] = {"parent": {"id": link.parent.id.val}}
            annotations.append(ann)

    return annotations


def process_dataset_to_csv(conn, dataset, base_url):
    print(f"Processing dataset {dataset.id}")
    export_file = f"Dataset:{dataset.id}_bff.csv"
    if os.path.exists(export_file):
        print(f"File {export_file} already exists, skipping...")
        return export_file
    image_ids = []
    images_by_id = {}
    for image in dataset.listChildren():
        image_ids.append(image.id)
        images_by_id[image.id] = {
            "dataset_name": dataset.getName(),
            "name": image.getName(),
            "date": image.creationEventDate().strftime("%Y-%m-%d %H:%M:%S.%Z"),
        }

    # Collect all the Keys...
    keys = set()

    # Add values to dict {image_id: {key: [list, of, values]}}
    kvp = {}

    batch_size = 100
    for i in range(0, len(image_ids), batch_size):
        print(f"Processing {i} to {i + batch_size}")
        batch_ids = image_ids[i : i + batch_size]
        anns = marshal_annotations(conn, image_ids=batch_ids, ann_type="map")
        for ann in anns:
            image_id = ann["link"]["parent"]["id"]
            if image_id not in kvp:
                kvp[image_id] = defaultdict(list)
            for key_val in ann["values"]:
                key = key_val[0]
                value = key_val[1]
                kvp[image_id][key].append(value)
                keys.add(key)

    column_names = ["File Path", "File Name", "Dataset", "Thumbnail"]
    column_names.extend(list(keys))
    column_names.append("Uploaded")

    # write csv e.g "Dataset:1_bff.csv"...
    with open(export_file, mode="w") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(column_names)
        for image_id in image_ids:
            values = kvp.get(image_id, {})
            thumb_url = f"{base_url}webgateway/render_thumbnail/{image_id}"
            image = conn.getObject("Image", image_id)
            # we end url with .png so that BFF enables open-with "Browser"
            image_url = f"{base_url}webclient/?show=image-{image_id}&_=.png"
            img_info = images_by_id.get(image_id)
            row = [
                image_url,
                img_info.get("name") if img_info else "Not Found",
                img_info.get("dataset_name") if img_info else "Not Found",
                thumb_url,
            ]
            for key in keys:
                row.append(",".join(values.get(key, [])))
            row.append(image.creationEventDate().strftime("%Y-%m-%d %H:%M:%S.%Z"))
            writer.writerow(row)

    return export_file


def export_to_bff(conn, script_params):
    """
    Export image Key-Value pairs to a csv or parquet file for Biofile Finder
    """

    max_datasets = 500
    base_url = script_params["Base_URL"]
    csv_names = []

    conn.SERVICE_OPTS.setOmeroGroup(-1)
    if script_params["Data_Type"] == "Project":
        parent = conn.getObject("Project", script_params["IDs"][0])
        group_id = parent.getDetails().group.id.val
        conn.SERVICE_OPTS.setOmeroGroup(group_id)
        for obj_id in script_params["IDs"]:
            datasets = list(conn.getObjects("Dataset", opts={"project": obj_id}))
            datasets.sort(key=lambda x: x.id)
            datasets = datasets[:max_datasets]
            for dataset in datasets:
                csv_name = process_dataset_to_csv(conn, dataset, base_url)
                csv_names.append(csv_name)
    elif script_params["Data_Type"] == "Dataset":
        parent = conn.getObject("Dataset", script_params["IDs"][0])
        group_id = parent.getDetails().group.id.val
        conn.SERVICE_OPTS.setOmeroGroup(group_id)
        for obj_id in script_params["IDs"]:
            dataset = conn.getObject("Dataset", obj_id)
            csv_name = process_dataset_to_csv(conn, dataset, base_url)
            csv_names.append(csv_name)

    # Finally, combine the csv files into a single file
    data_tables = [pa_csv.read_csv(csv_name) for csv_name in csv_names]
    combined_table = pa.concat_tables(data_tables, promote_options="default")
    combined_table.combine_chunks()

    print("combined_table", combined_table)
    # Write the combined table back to a Parquet file
    oids = "_".join([str(i) for i in script_params["IDs"]])
    export_file = f"{script_params['Data_Type']}_{oids}_bff.parquet"
    pq.write_table(combined_table, export_file)

    if not parent.canAnnotate():
        msg = f"{export_file} created but not linked to {parent.getName()}"
        return None, msg

    file_annotation, message = script_utils.create_link_file_annotation(
        conn,
        export_file,
        parent,
        namespace=BFF_NAMESPACE,
        mimetype="application/vnd.apache.parquet",
    )
    return file_annotation, message


def run_script():
    """
    The main entry point of the script, as called by the client via the
    scripting service, passing the required parameters.
    """

    data_types = [rstring("Project"), rstring("Dataset")]

    client = scripts.client(
        "Export_to_Biofile_Finder.py",
        """Export image Key-Value pairs to parquet file for Biofile Finder""",
        scripts.String(
            "Data_Type",
            optional=False,
            grouping="1",
            description="The data you want to work with.",
            values=data_types,
            default="Project",
        ),
        scripts.List(
            "IDs",
            optional=False,
            grouping="2",
            description="List of Dataset IDs or Image IDs",
        ).ofType(rlong(0)),
        scripts.String(
            "Base_URL",
            optional=True,
            grouping="3",
            description=(
                "The full or relative URL to OMERO.web" " e.g. https://server.com/ or /"
            ),
            default="/",
        ),
        # This allows clients to query the script version
        # by listing script params and getting default value
        scripts.String("VERSION", default=VERSION),
        authors=["William Moore", "OME Team"],
        institutions=["University of Dundee"],
    )

    try:
        start_time = datetime.now()
        script_params = {}

        conn = BlitzGateway(client_obj=client)

        script_params = client.getInputs(unwrap=True)
        print("Script parameters: %s" % str(script_params))

        # call the main script - returns a file annotation wrapper
        file_annotation, message = export_to_bff(conn, script_params)

        stop_time = datetime.now()
        print("Duration: %s" % str(stop_time - start_time))

        # return this fileAnnotation to the client.
        client.setOutput("Message", rstring(message))
        if file_annotation is not None:
            client.setOutput("File_Annotation", robject(file_annotation._obj))

    finally:
        client.closeSession()


if __name__ == "__main__":
    try:
        run_script()
    except ClientError:
        # This is a workaround for the fact that the script is not run in a
        # session, so we need to create one manually.
        from omero.cli import cli_login

        with cli_login() as cli:

            # use argparse to get the project id
            parser = argparse.ArgumentParser()
            parser.add_argument("target", help="E.g 'Project:123' or 'Dataset:123'")
            parser.add_argument(
                "--base-url",
                help=(
                    "The full or relative URL to OMERO.web"
                    " e.g. https://server.com/ or /"
                ),
                default="/",
            )
            args = parser.parse_args()
            dtype, obj_id = args.target.split(":")
            obj_ids = [int(i) for i in obj_id.split(",")]

            conn = BlitzGateway(client_obj=cli._client)
            script_params = {
                "Data_Type": dtype,
                "IDs": obj_ids,
                "Base_URL": args.base_url,
            }
            file_annotation, message = export_to_bff(conn, script_params)
            print("Message: %s" % message)
