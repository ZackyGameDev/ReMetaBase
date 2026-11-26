
# project root files
First of all, we're using something called "Docker" to setup the external databases.

The databases are three:

1 is the metabase catalogue. basically the database that metabase itself will use to store data

then two others, which are the demo databases. one uses mysql the other is postgres.

all three are hosted on local host using docker. .sql and .yml file in the project root directory are for that

start_demo.ps1 just starts the servers of the databases.

# backend

is at `backend/`. uses fastapi. connection builder 