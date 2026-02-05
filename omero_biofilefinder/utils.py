#!/usr/bin/env python
#
# Copyright (c) 2026 University of Dundee.
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

from copy import deepcopy

from omero.rtypes import rlong, unwrap, wrap
from omero.sys import ParametersI


def get_image_count(conn, obj_type, obj_id):
    """
    Count images for the specified object.

    @param conn:        BlitzGateway
    @param obj_type:    Type of object, e.g. 'Project' or 'Dataset'
    @param obj_id:      ID of the object
    @return             Image count
    """
    ctx = deepcopy(conn.SERVICE_OPTS)
    ctx.setOmeroGroup(-1)
    params = ParametersI()
    params.add("id", wrap(rlong(obj_id)))
    if obj_type == "Dataset":
        query = (
            "select count(link.id) from DatasetImageLink link"
            " where link.parent.id = :id"
        )
    elif obj_type == "Project":
        query = (
            "select count(link.id) from DatasetImageLink link"
            " join link.parent as ds"
            " join ds.projectLinks as pl"
            " where pl.parent.id = :id"
        )
    elif obj_type == "Plate":
        query = (
            "select count(ws.id) from WellSample ws"
            " join ws.well as well"
            " join well.plate as plate"
            " where plate.id = :id"
        )
    result = conn.getQueryService().projection(query, params, ctx)
    count = 0
    for d in result:
        count = unwrap(d[0])
    return count
