# Docker deployment

The `docker` folder contains the scripts and configurations required to build the application's Docker image
and deploy the application using docker-compose.

## Docker compose

Specifically, the `docker/docker-compose-core.yml` script launches the containers with the application's services, 
while the  `docker/docker-compose-proxy.yml` script launches the Nginx and CertBot containers to handle url 
redirect and SSL certificate renewal. When launching on a host/VM that already has nginx installed or an equivalent
proxy. only the core features are required.

To start properly, the application requires
- a certificate generated by Let's Encrypt (if starting with Nginx/Certbot).
- the environment variables to be set in the shell.
- the production and test databases (`icubam.db` and `test.db`) to be generated and available by default at the root 
of the project (note: while both must be present to be mounted, only one will be used). 
- the configuration file (default name: ``config.toml``)


### Nginx/Certbot setup

The LetsEncryot/certbot setup is based on https://github.com/wmnnd/nginx-certbot.

This procedure must be executed on the VM/host that will serve the application, and must be Internet reachable.

For a full deployment with Nginx/Certbot, you need to first update some configuration files (`init-letsencrypt.sh` and the Nginx config files), 
then launch the `init-letsencrypt.sh` script to start the nginx/certbot containers and generate the ssl certificate for the host,  
then relaunch the nginx container with the final configuration along with the other containers.

```
# edit the email address associated to the SSL certificate to be created
vi ./docker/scripts/init-letsencrypt.sh

# Update the Web hostname to use
cd ./docker/scripts
./set_hostname_nginx.sh www.myhostname.org

# Copy the config file and generate certificate
cd ../..
cp ./docker/configs/nginx/app.conf.init ./docker/configs/nginx/app.conf
./docker/scripts/init-letsencrypt.sh

# terminate the nginx container (required to proprerly reload the config).
docker rm -f icubam_nginx_1

# Set nginx configuration (dev or prod) for icubam
cp ./docker/configs/nginx/app.conf.init ./docker/configs/nginx/app.conf

# launch the app
```

The `configs` folder stores nginx/certbot configuration files for ssl connection support.
Certbot configuration files are added at runtime when generating/updating the ssl certificate.

Three nginx configurations are provided,
- an `init` configuration used in the first initialization step of the Let'sEncryp/Certbot setup.
- a `dev` file for testing locally, that only supports http (change https to http
and remove port 8888 when using the link provided by the running server).
- a `prod` file that manages ssl connections for testing on an internet reachable host. Depending on the deployment server name, changes to the `nginx/app.conf` file are required.
In particular, WEB_HOSTNAME should be replaced with the targeted's URL hostname (e.g., www.example.org)
for both the `server_name` and also in the path for the ssl certificates.

Compared to the initial `init-letsencrypt.sh`script, explicit setup of the docker-compose-proxy.yml file and root path has been added as all the docker related files are in a specific subfolder.

### Environment variables

Two kinds of environment variables must be set
- env. variables used by the app's services
- env. variables used by docker/docker-compose when building/launching the application 

**Docker's environement variables**

- ICUBAM_COMPOSE_CONTEXT: root folder for the build context
- ICUBAM_CONFIG_PATH: path/filename for the app's configuration file
- ICUBAM_PROD_DB_PATH: path/filename for the production database
- ICUBAM_TEST_DB_PATH: path/filename for the test database
- ICUBAM_CERTBOT_PATH: location for the CertBot configuration and result files
- ICUBAM_NGINX_PATH: location for the Nginx configuration files
- IMAGE_NAME: name of the Docker image to use
- IMAGE_TAG: tag of the Docker image to use
- LOGS_DIR: folder where log files (e.g., './logs')

Files/folders are mounted (bind) in the containers (nginx/certbot) defined in the `docker-compose-proxy.yml`file.

**Application's environement variables**

The containers expect the following variables to be set in order to launch

- ENV_MODE (can be prod or dev)
- SECRET_COOKIE
- JWT_SECRET
- GOOGLE_API_KEY
- TW_KEY
- TW_API

These environment variables are not set in the Docker image, but must be set when starting the 
docker/docker-compose command.  Also check the [install.md](./install.md) documentation for more details.

Change the environement variable `ENV_MODE` depending on the targeted environment (dev, prod, ...) .
The `--mode=$ENV_MODE` command line parameter when starting the icubam server and sms apps is set using this environment
variable (check the files `start_server.sh` and `start_server_sms.sh`).

**Setting up environement variables**

There are multiple ways for setting up these variables.
- have a .env file at the root of the project
- set the environment variables in the shell (e.g., to manage different configurations stored in another location)
prior to launching the docker-compose.

Rules of precedence
- If variables **not set** in the environment and **no .env file**, the containers start and fail due to missing variables.
- If variables **set** in the environment but **no .env file**, the containers start with the values set in the environment variables.
- If variables **not set** in the environment but **.env file** exists, the containers start with the values set in the .env file.
- If variables **set** in the environment and **.env file** exists, the containers start with the values set in the environment variables.

Note: in order to reload the configuration, the containers (not the image) must be terminated and recreated (not only stop/start).

Note: One way to quickly set environment variables from a dedicated file
```
set -a
. $HOME/icubam_config/envars-production.env
set -a
```

### Database setup

Check the [install.md](./install.md) documentation.

### Launching the app

The application is launched, either in a full version that also starts nginx and certbot for managing ssl connections, 
or just the containers for the app in case the host/environment already provides SSL/proxy (or for local testing)

```
## Full setup
docker-compose -f docker/docker-compose-core.yml -f docker/docker-compose-proxy.yml --project-directory .  up

## App-only setup
docker-compose -f docker/docker-compose-core.yml --project-directory .  up
```

By default, the application is launched from the root of the project (and not from the folder containing the 
compose files). 

By default, the databases and config files/folders are within the project's folder. To mount files/folders that are 
outside, the `ICUBAM_COMPOSE_CONTEXT` variable must be set accordingly.

To launch the application is detached mode, add ``-d`` flas in the docker-compose command. 

Note: a convenience script (`./docker/scripts/status.sh` is available to check that the containers have properly 
started.


### Docker commands

Remove/force the icubam containers
```
docker rm -f icubam-server icubam-sms-server icubam-bo-server
```

Get the logs of the server
```
docker logs icubam-server
```

Delete the server container image
```
docker rmi icubam
```

To debug a container, in another shell, enter the server container in interactive mode to check the files are at their expected location (using `docker exec -it icubam-server bash` or
```
docker exec -it icubam-server "ls -la"`
docker exec -it icubam-server bash`
```

### Starting containers separately

The `docker` folder also contains scripts to launch the application' containers individually (for debugging purposes, 
using the `docker/scripts/docker_build.sh`, `docker/scripts/docker_run.sh` and `docker/scripts/docker_sms_build.sh` 
scripts),

### Issues and debug

- If configuration files are updated, it is recommended to delete the existing containers et restart them to properly reload the configuration files.
- Check the files being mounted do exists (if they do not exist, a folder with this name is automatically created by compose).
- If changes done to configuration files are not visible/performed, check that the proper yml file is used in compose
