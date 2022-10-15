#!/bin/bash
OUTFILE="./data/albums.json"
# URL=https://icloud-api.evolution-web.de/album
URL=http://localhost:8000/album
ALBUMS=$(grep album  content/post/*.md | cut -d " " -f 2)
# A=$(grep album  content/post/*.md | cut -d " " -f 2)
echo '{"dummystart": "dummy"' > $OUTFILE
for album in $ALBUMS; do 
    echo Fetching Album $URL/$album
    data=$(curl $URL/$album) 2> /dev/null
    if [[ $data ]]; then
        echo "Found Album"
        echo ",\"$album\":$data" >> $OUTFILE
    else
        echo Did not find Album
    fi
done
echo "}" >> $OUTFILE

# for name in $A; do echo $name; done