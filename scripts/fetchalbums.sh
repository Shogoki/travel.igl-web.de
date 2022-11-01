#!/bin/bash
OUTFILE="./data/albums.json"
URL=https://icloud-api.evolution-web.de/album
# URL=http://localhost:8000/album
ALBUMS=$(grep album  content/post/*.md | cut -d " " -f 2)
echo '{"dummystart": "dummy"' > $OUTFILE
for album in $ALBUMS; do 
    echo Fetching Album $URL/$album
    data=$(curl --fail $URL/$album) 2> /dev/null
    if [[ $data ]]; then
        echo "Found Album $album"
        length=$(echo $data | jq length)
        echo ",\"$album\":$data" >> $OUTFILE
        for (( i=0; i<=$length; i++ ))
        do
            thumb=$(echo "$data" | jq -r ".[$i].thumbnailUrl")
            full=$(echo "$data" | jq -r ".[$i].fullImageUrl")
            curl -o "static/img/thumbs/${album}_$i.jpg" $thumb
            curl -o "static/img/full/${album}_$i.jpg" $full
        done

    else
        echo Did not find Album
    fi
done
echo "}" >> $OUTFILE