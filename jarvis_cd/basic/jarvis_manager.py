"""
This module represents the JarvisCD Manager singleton. It stores an index
of all relevant paths needed by most jarvis repos.
"""

import pathlib
import os
from jarvis_util.shell.filesystem import Rm
from jarvis_util.serialize.yaml_file import YamlFile
from jarvis_util.util.import_mod import load_class
from jarvis_util.util.naming import to_camel_case
from jarvis_util.util.expand_env import expand_env
from jarvis_util.util.hostfile import Hostfile
from jarvis_util.introspect.system_info import ResourceGraph
from jarvis_util.shell.filesystem import Mkdir
from jarvis_util.shell.pssh_exec import PsshExecInfo
from jarvis_util.shell.local_exec import LocalExecInfo
from pathlib import Path
import getpass
import yaml
import sys


class JarvisManager:
    """
    This class stores relevant paths and loads global configurations for
    internal use by jarvis repos.
    """
    instance_ = None

    @staticmethod
    def get_instance():
        if JarvisManager.instance_ is None:
            JarvisManager.instance_ = JarvisManager()
        return JarvisManager.instance_

    def __init__(self):
        self.jarvis_root = str(
            pathlib.Path(__file__).parent.parent.parent.resolve())
        # The current user
        self.user = getpass.getuser()
        # Where Jarvis stores pipeline data (per-user)
        self.config_dir = None
        # The path to named Jarvis environment caches
        self.env_dir = None
        # Where Jarvis stores data locally to a pkg (per-user)
        self.private_dir = None
        # Where Jarvis stores data in a shared directory (per-user)
        self.shared_dir = None
        # The current pipeline (per-user)
        self.cur_pipeline = None
        # Path to local jarvis configuration directory
        self.local_config_dir = os.path.join(Path.home(), '.jarvis')
        # Path to local jarvis builtin package directory
        self.builtin_dir = os.path.join(self.local_config_dir, 'builtin')
        # Path to the global jarvis configuration
        self.jarvis_conf_path = os.path.join(self.local_config_dir,
                                             'jarvis_config.yaml')
        # Path to the global jarvis resource graph
        self.jarvis_repos_path = os.path.join(self.local_config_dir,
                                              'repos.yaml')
        # The Jarvis configuration (per-user)
        self.jarvis_conf = None
        #  The path to the jarvis resource graph (global across users)
        self.resource_graph_path = os.path.join(self.local_config_dir,
                                                'resource_graph.yaml')
        # The Jarvis resource graph (global across users)
        self.resource_graph = None
        self.hostfile = None
        self.repos = []
        self.load()

    def create(self, config_dir, private_dir, shared_dir=None):
        """
        Create a new root jarvis config under config/$USER/jarvis_config.yaml

        :param config_dir: the directory where jarvis stores pipeline
        metadata
        :param private_dir: a directory which is shared on all pkgs, but
        stores data privately to the pkg
        :param shared_dir: a directory which is shared on all pkgs, where
        all pkgs have the same view of the data
        :return: None
        """
        self.config_dir = expand_env(config_dir)
        self.private_dir = expand_env(private_dir)
        self.shared_dir = expand_env(shared_dir)
        self.jarvis_conf = {
            # Global parameters
            'CONFIG_DIR': config_dir,
            'PRIVATE_DIR': private_dir,
            'SHARED_DIR': shared_dir,
            'REPOS': [],

            # Per-user parameters
            'HOSTFILE': None,
            'CUR_PIPELINE': None,
        }
        self.load_repos()
        self.resource_graph = ResourceGraph()
        self.hostfile = Hostfile()
        os.makedirs(self.local_config_dir, exist_ok=True)
        self.save()

    def load_repos(self):
        # Read repos conf
        if os.path.exists(self.jarvis_repos_path):
            self.repos = YamlFile(self.jarvis_repos_path).load()['REPOS']
        else:
            self.repos = [
                {
                    'path': self.builtin_dir,
                    'name': 'builtin'
                }
            ]

    def load(self):
        """
        Load the jarvis config from config/jarvis_config.yaml

        :return: None
        """
        if not os.path.exists(self.jarvis_conf_path):
            print('No configuration was found. Run jarvis init or bootstrap. '
                  'If you are currently running those commands, '
                  'please ignore this message.')
            return
        self.jarvis_conf = {}
        # Read global jarvis conf
        self.jarvis_conf.update(YamlFile(self.jarvis_conf_path).load())
        # Read repos files
        self.load_repos()
        # Get the various jarvis paths
        self.config_dir = expand_env(self.jarvis_conf['CONFIG_DIR'])
        self.env_dir = os.path.join(self.config_dir, 'env')
        os.makedirs(f'{self.config_dir}', exist_ok=True)
        os.makedirs(f'{self.env_dir}', exist_ok=True)
        self.private_dir = expand_env(self.jarvis_conf['PRIVATE_DIR'])
        Mkdir(self.private_dir,
              PsshExecInfo(hostfile=self.hostfile))
        if self.jarvis_conf['SHARED_DIR'] is not None:
            self.shared_dir = expand_env(self.jarvis_conf['SHARED_DIR'])
            os.makedirs(f'{self.shared_dir}', exist_ok=True)
        # Read global resource graph
        if os.path.exists(self.resource_graph_path):
            self.resource_graph = ResourceGraph().load(self.resource_graph_path)
        else:
            self.resource_graph = ResourceGraph()
        self.cur_pipeline = self.jarvis_conf['CUR_PIPELINE']
        try:
            self.hostfile = Hostfile(hostfile=self.jarvis_conf['HOSTFILE'])
        except Exception as e:
            print(f"Failed to open hostfile {self.jarvis_conf['HOSTFILE']}")
            self.hostfile = Hostfile()
            print(f"An error occurred: {e}")
            print(f"Error arguments: {e.args}")
        return self

    def save(self):
        """
        Save the jarvis config to config/jarvis_config.yaml

        :return: None
        """
        # Update jarvis conf
        if self.jarvis_conf:
            self.jarvis_conf['CUR_PIPELINE'] = self.cur_pipeline
            self.jarvis_conf['HOSTFILE'] = self.hostfile.path
        # Update repos
        YamlFile(self.jarvis_repos_path).save({'REPOS': self.repos})
        # Save global resource graph
        if self.resource_graph:
            self.resource_graph.save(self.resource_graph_path)
        # Save global and per-user conf
        if self.jarvis_conf:
            YamlFile(self.jarvis_conf_path).save(self.jarvis_conf)

    def set_hostfile(self, path):
        """
        Set the hostfile and re-configure all existing jarvis pipelines

        :return: None
        """
        if len(path) > 0:
            self.hostfile = Hostfile(hostfile=path)
        else:
            self.hostfile = Hostfile()

    def bootstrap_from(self, machine):
        """
        Bootstrap jarvis for a particular machine

        :param machine: The machine config to copy
        :return: None
        """
        if machine == 'local':
            #
            self.create(
                os.path.join(self.local_config_dir, 'config'),
                os.path.join(self.local_config_dir, 'private'),
                os.path.join(self.local_config_dir, 'shared'))
            self.save()
            return
        self.load_repos()
        os.makedirs(self.local_config_dir, exist_ok=True)
        config_path = f'{self.builtin_dir}/config/{machine}.yaml'
        if os.path.exists(config_path):
            config = expand_env(YamlFile(config_path).load())
            new_config_path = f'{self.local_config_dir}/jarvis_config.yaml'
            YamlFile(new_config_path).save(config)

        rg_path = f'{self.builtin_dir}/resource_graph/{machine}.yaml'
        if os.path.exists(rg_path):
            self.resource_graph = ResourceGraph().load(rg_path)
            new_rg_path = f'{self.local_config_dir}/resource_graph.yaml'
            self.resource_graph.save(new_rg_path)

    def bootstrap_list(self):
        """
        List machines we can bootstrap with no additional configuration

        :return: None
        """
        configs = os.listdir(f'{self.builtin_dir}/config')
        for config in configs:
            print(config.replace('.yaml', ''))

    def reset(self):
        """
        Destroy all pipelines and their data

        :return: None
        """
        Rm(self.shared_dir, LocalExecInfo())
        Rm(self.private_dir, PsshExecInfo(
            hostfile=self.hostfile))

    def print_config(self):
        print(yaml.dump(self.jarvis_conf))

    def print_config_path(self):
        print(self.jarvis_conf_path)

    def resource_graph_show(self):
        """
        Print the resource graph

        :return: None
        """
        print('fs:')
        self.resource_graph.print_df(self.resource_graph.fs)
        print('net:')
        self.resource_graph.print_df(self.resource_graph.net)

    def resource_graph_build(self, net_sleep):
        """
        Introspect the system and construct a resource graph.

        :return: None
        """
        self.resource_graph = ResourceGraph()
        self.resource_graph.build(
            PsshExecInfo(hostfile=self.hostfile), net_sleep=net_sleep)

    def resource_graph_modify(self, net_sleep):
        """
        Modify the resource graph to retest resources
        """
        self.resource_graph.modify(
            PsshExecInfo(hostfile=self.hostfile), net_sleep=net_sleep)

    def list_pipelines(self):
        """
        Get a list of all created pipelines

        :return: List of pipelines
        """
        pipelines = os.listdir(self.config_dir)
        pipelines.sort()
        pipelines.remove('env')
        return pipelines

    def cd(self, pipeline_id):
        """
        Make jarvis focus on the pipeline with id ID.
        This pipeline will be used for subsequent operations:
            jarvis [start/stop/clean/stop/destroy/configure]

        :param pipeline_id: The id of the pipeline to focus on
        :return: None
        """
        self.cur_pipeline = pipeline_id

    def add_repo(self, path, force):
        """
        Induct a repo into the jarvis managers repo search variable.
        Does not create data on the filesystem.

        :param path: The path to the repo to induct. The basename of the
        repo is assumed to be its repo name. E.g., for /home/hi/myrepo,
        my repo would be the basename.
        :param force: If true, overwrite the repo if it already exists
        :return: None
        """

        repo_name = os.path.basename(path)
        path = os.path.abspath(path)
        for repo in self.repos:
            if repo['name'] == repo_name:
                if os.path.exists(repo['path']) and not force:
                    print('Warning: repo already exists. '
                          'Use --force to overwrite')
                    return
                else:
                    repo['path'] = path
                    print(f'Updated the {repo_name} to path {path}')
                    return
        if not os.path.exists(os.path.join(path, repo_name)):
            print('Error: repo must have a subdirectory with the same name')
            sys.exit(1)
        self.repos.insert(0, {
            'path': path,
            'name': repo_name
        })
        print(f'Added the {repo_name} repo')

    def create_pkg(self, pkg_cls, pkg_type):
        """
        Creates the skeleton of a pkg within the primary repo.

        :param pkg_type: The name of the pkg to create
        :param pkg_cls: The type of pkg to create (Service, Application, etc.)
        :return: None
        """
        # load the template data
        tmpl_dir = f'{self.jarvis_root}/jarvis_cd/template'
        with open(f'{tmpl_dir}/{pkg_cls}_templ.py',
                  encoding='utf-8') as fp:
            text = fp.read()

        # Replace MyPkg with the pkg name
        text = text.replace('MyPkg', to_camel_case(pkg_type))

        # Write the specialized data
        repo_name = self.repos[0]['name']
        repo_dir = self.repos[0]['path']
        pkg_path = f'{repo_dir}/{repo_name}/{pkg_type}'
        os.makedirs(pkg_path, exist_ok=True)
        with open(f'{pkg_path}/pkg.py', 'w',
                  encoding='utf-8') as fp:
            fp.write(text)

    def get_repo(self, repo_name):
        """
        Get the repo information associated with the repo name

        :param repo_name: The repo to get info for
        :return: A dictionary containing the name of the repo and the
        path to the repo
        """

        matches = [repo for repo in self.repos if repo_name == repo['name']]
        if len(matches) == 0:
            return None
        return matches[0]

    def promote_repo(self, repo_name):
        """
        Make all subsequent jarvis append operations track this repo first.

        :param repo_name: The repo to prioritize
        :return: None
        """

        main_repo = self.get_repo(repo_name)
        if main_repo is None:
            raise Exception(f'Could not find repo: {repo_name}')
        self.repos = [repo for repo in self.repos if repo_name != repo['name']]
        self.repos.insert(0, main_repo)

    def remove_repo(self, repo_name):
        """
        Remove a repo from consideration. Does not destroy data.

        :param repo_name: the name of the repo to remove
        :return: None
        """
        self.repos = [repo for repo in self.repos if repo_name != repo['name']]

    def list_repos(self):
        """
        Print all repos in jarvis

        :return: None
        """
        for repo in self.repos:
            self.list_repo(repo['name'])


    def list_repo(self, repo_name):
        """
        List all of the pkgs in a repo

        :param repo_name: The repo to list
        :return: None
        """
        repo = self.get_repo(repo_name)
        if not os.path.exists(repo['path']):
            print(f"Repo {repo['path']} does not exist")
            return
        pkg_types = os.listdir(os.path.join(repo['path'], repo['name']))
        pkg_types.sort()
        print(f"{repo['name']}: {repo['path']}")
        for pkg_type in pkg_types:
            if not pkg_type.startswith('_'):
                print(f'  {pkg_type}')

    def construct_pkg(self, pkg_type):
        """
        Construct a pkg by searching repos for the pkg type

        :param pkg_type: The type of pkg to load (snake case).
        :return: A object of type "pkg_type"
        """
        for repo in self.repos:
            cls = load_class(f"{repo['name']}.{pkg_type}.pkg",
                             repo['path'],
                             to_camel_case(pkg_type))
            if cls is None:
                continue
            return cls()
