#!/bin/bash
# init
# =============================================
# SCRIPT COMMANDS
echo
echo "===============  START HUMMINGBOT INSTANCE ==============="
echo
echo "List of all docker instances:"
echo
docker ps -a
echo
echo
read -p "   Enter the NAME of the Hummingbot instance to start or connect to (default = \"hbot01\") >>> " INSTANCE_NAME
if [ "$INSTANCE_NAME" == "" ]
then
  INSTANCE_NAME="hbot01"
fi
echo
# =============================================
# EXECUTE SCRIPT
docker start $INSTANCE_NAME && docker attach $INSTANCE_NAME
