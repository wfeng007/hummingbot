#!/bin/bash
# init
# =============================================
# SCRIPT COMMANDS
echo
echo "===============  CONNECT HUMMINGBOT INSTANCE ==============="
echo
echo "List of all docker instances:"
echo
docker ps -a
echo
echo
read -p "   Enter the NAME of the Hummingbot instance to connect/attach to (default = \"hbot01\") >>> " INSTANCE_NAME
if [ "$INSTANCE_NAME" == "" ]
then
  INSTANCE_NAME="hbot01"
fi
echo
# =============================================
# EXECUTE SCRIPT
docker attach $INSTANCE_NAME

