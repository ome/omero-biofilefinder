#!/bin/bash
# output dir is biofile-finder/packages/web/dist/ - Need to copy into omero_biofilefinder...
# static assets includes index.html, js & css etc.
echo "Copying bff static assets (index.html, js & css) to omero_biofilefinder/static/omero_biofilefinder/dist/"
mkdir -p omero_biofilefinder/static/omero_biofilefinder/dist/
cp biofile-finder/packages/web/dist/* omero_biofilefinder/static/omero_biofilefinder/dist/
