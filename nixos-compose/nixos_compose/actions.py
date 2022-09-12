import json
import os
import os.path as op
import glob
import socket
import sys
import subprocess
import time
import base64
import click
import signal
import psutil
import itertools
import ipaddress
from halo import Halo
from .tools.kataract import generate_scp_tasks, exec_kataract_tasks


##
# Helper function to determine fstype
#
def get_fs_type(path):
    root_type = ""
    for part in psutil.disk_partitions(True):
        if part.mountpoint == "/":
            root_type = part.fstype
            continue
        if path.startswith(part.mountpoint):
            return part.fstype
    return root_type


##
# Retrieve from path from different store location if needed
#


def realpath_from_store(ctx, path):
    p = op.realpath(path)
    for store_path in ctx.alternative_stores:
        new_p = f"{store_path}{p[4:]}"
        if op.exists(new_p):
            return new_p
    if op.exists(p):
        return p
    ctx.elog(f"{path} does not exist in standard store or alternate")
    sys.exit(1)


##
# Generate/manipulate/copy deploy, compose files
#
def get_deployment_file(ctx, deployment_file):
    def exit_is_not_file(f):
        if not op.isfile(f):
            ctx.elog(f"{f} is not a file, deployment_file option must be provided")
            sys.exit(1)

    if not deployment_file:
        deployment_file = max(
            glob.glob(f"{op.join(ctx.envdir, 'deploy')}/*"), key=op.getctime
        )
        exit_is_not_file(deployment_file)
        return deployment_file
    else:
        base_deployment_file = deployment_file
        if op.exists(deployment_file):
            exit_is_not_file(deployment_file)
            return deployment_file
        else:
            deployment_file = op.join(op.join(ctx.envdir, "deploy"), deployment_file)
            if op.exists(deployment_file):
                exit_is_not_file(deployment_file)
                return deployment_file
            else:
                ctx.elog(f"{base_deployment_file} not found")
                sys.exit(1)


def read_deployment_info(ctx, deployment_file=None):
    ctx.deployment_filename = get_deployment_file(ctx, deployment_file)
    with open(ctx.deployment_filename, "r") as f:
        deployment_info = json.load(f)
    ctx.deployment_info = deployment_info
    return


def read_deployment_info_str(ctx, deployment_file=None):
    ctx.deployment_filename = get_deployment_file(ctx, deployment_file)
    with open(ctx.deployment_filename, "r") as f:
        deployment_info_str = f.read()
    return deployment_info_str


def read_test_script(ctx, compose_info_or_str):
    if isinstance(compose_info_or_str, str):
        filename = compose_info_or_str
    elif "test_script" in compose_info_or_str:
        filename = compose_info_or_str["test_script"]
    else:
        return None
    with open(realpath_from_store(ctx, filename), "r") as f:
        test_script = f.read()
        return test_script


def read_compose_info(ctx):

    if not op.isfile(ctx.compose_info_file):
        raise click.ClickException(f"{ctx.compose_info_filename} does not exist")
    with open(ctx.compose_info_file, "r") as f:
        compose_info = json.load(f)

    if "compositions_info" in compose_info:
        ctx.compositions_info = compose_info

        if len(compose_info["compositions_info"]) > 1:
            ctx.multiple_compositions = True
            ctx.vlog(
                f"Image with multiple compositions is detected, selected composition: {ctx.composition_name}"
            )

        if (
            ctx.composition_name
            and ctx.composition_name not in ctx.compositions_info["compositions_info"]
        ):
            ctx.elog(
                f'Composition named: "{ctx.composition_name}" is not in {ctx.compose_info_file}'
            )
            if ctx.composition_name == ctx.composition_basename_file:
                ctx.elog(
                    "When image contains multiple compositions and without default, the one to launch must by indicated via composition name option with name as prefix followed by file's label separated by ::  (e.g. -c foo::g5k-ramdisk). The default if present, is composition with its name equals to file's label."
                )
            sys.exit(1)
        if ctx.composition_name:
            compose_info = ctx.compositions_info["compositions_info"][
                ctx.composition_name
            ]
        compose_info["all"] = ctx.compositions_info["all"]
        compose_info["flavour"] = ctx.compositions_info["flavour"]

        compose_flavour_name = ctx.compositions_info["flavour"]["name"]
        if ctx.flavour.name != compose_flavour_name:
            raise click.ClickException(
                f"Selected flavour ({ctx.flavour.name}) differs from compose info ({compose_flavour_name})"
            )

    ctx.compose_info = compose_info
    return


def read_hosts(hostsfile):
    return [host.rstrip() for host in open(hostsfile, "r")]


def translate_hosts2ip(ctx, hosts):
    for host in hosts:
        if host and (host not in ctx.host2ip_address):
            ip = socket.gethostbyname_ex(host)[2][0]
            ctx.host2ip_address[host] = ip
            ctx.ip_addresses.append(ip)
    return


def populate_deployment_vm_by_ip(nodes_info, roles_quantities):
    roles_quantities = health_check_roles_quantities(nodes_info, roles_quantities)
    i = 0
    deployment = {}
    ips = []
    for role, v in nodes_info.items():
        for hostname in roles_quantities[role]:
            ip = "10.0.2.{}".format(15 + i)
            ips.append(ip)
            # deployment[ip] = {"role": role, "vm_id": i}
            deployment[ip] = {
                "role": role,
                "init": v["init"],
                "vm_id": i,
                "host": hostname,
            }
            if "qemu_script" in v:
                deployment[ip]["qemu_script"] = v["qemu_script"]
            i = i + 1

    return deployment, ips


def health_check_roles_quantities(nodes_info, roles_quantities):
    if len(roles_quantities) == 0:
        # If no info we take one node per role
        roles_quantities = {role: [role] for role in nodes_info.keys()}
    else:
        # Step 1: if the user only gave the number of nodes of the roles
        for role in roles_quantities:
            if type(roles_quantities[role]) == int:
                nb_nodes = roles_quantities[role]
                if nb_nodes == 1:
                    roles_quantities[role] = [f"{role}"]
                else:
                    roles_quantities[role] = [f"{role}{i}" for i in range(1, nb_nodes + 1)]

        # Step 2: check that we do not have any conflict on the hostnames

        all_hostnames = list(itertools.chain.from_iterable(roles_quantities.values()))
        set_hostnames = set(all_hostnames)
        if len(all_hostnames) != len(set_hostnames):
            raise Exception("Conflict in the naming of the node")
    return roles_quantities


def populate_deployment_ips(nodes_info, ips, roles_quantities):
    roles_quantities = health_check_roles_quantities(nodes_info, roles_quantities)
    i = 0
    deployment = {}
    for role, v in nodes_info.items():
        for hostname in roles_quantities[role]:
            ip = ips[i]
            deployment[ip] = {"role": role, "host": hostname, "init": v["init"]}
            i = i + 1
    return deployment


def populate_deployment_forward_ssh_port(nodes):
    i = 0
    deployment = {}
    for role in nodes:
        ip0 = 1 + i
        ip = f"127.0.0.{ip0}"
        deployment[ip] = {"host": role, "role": role, "ssh-port": 22022 + i}
        i = i + 1

    return deployment


def generate_deployment_info(ctx, ssh_pub_key_file=None):
    if not ctx.compose_info:
        read_compose_info(ctx)

    if not ssh_pub_key_file:
        ssh_pub_key_file = os.environ["HOME"] + "/.ssh/id_rsa.pub"
    with open(ssh_pub_key_file, "r") as f:
        sshkey_pub = f.read().rstrip()

    if ctx.forward_ssh_port and sshkey_pub:
        # QEMU_KERNEL_PARAMS environement variable is used to give ssh pub
        # key with nixos-test-ssh flavour (see boot.initrd.postMountCommands in nix/base.nix)
        qemu_kernel_params = ""
        if "QEMU_KERNEL_PARAMS" in os.environ:
            qemu_kernel_params = os.environ["QEMU_KERNEL_PARAMS"]
        ssh_key_pub_b64 = base64.b64encode(sshkey_pub.encode()).decode()
        os.environ[
            "QEMU_KERNEL_PARAMS"
        ] = f"{qemu_kernel_params} ssh_key.pub:{ssh_key_pub_b64}"

    # if ctx.multiple_compositions:  :: TO REMOVE ???
    #    nodes = ctx.compose_info["nodes"]

    if ctx.ip_addresses:
        deployment = populate_deployment_ips(
            ctx.compose_info["nodes"], ctx.ip_addresses, ctx.roles_quantities
        )
    elif ctx.forward_ssh_port:
        deployment = populate_deployment_forward_ssh_port(ctx.compose_info["nodes"])
    else:
        deployment, ctx.ip_addresses = populate_deployment_vm_by_ip(
            ctx.compose_info["nodes"], ctx.roles_quantities
        )
        deployment = {
            k: {
                "role": v["role"],
                "host": v["host"],
                "vm_id": v["vm_id"],
                "init": v["init"] if "host" in v else v["role"],
            }
            for k, v in deployment.items()
        }
    deployment = {
        "ssh_key.pub": sshkey_pub,
        "deployment": deployment,
    }

    if "all" in ctx.compose_info:
        deployment["all"] = ctx.compose_info["all"]

    if ctx.composition_name:
        deployment["composition"] = ctx.composition_name

    # Add user, used to determine nfs mount path on Grid'5000 by example
    # TODO: add option to override this (
    deployment["user"] = os.environ["USER"]

    # for k in ["all", "flavour"]:
    #    if k in compose_info:
    #        deployment[k] = compose_info[k]

    # If there is too much nodes httpd must used to tranfert deployment info,
    # due to kernel parameter size limit (deployment_info_b64 certainly will exceed it)
    if len(deployment["deployment"]) > 4:
        ctx.use_httpd = True

    json_deployment = json.dumps(deployment, indent=2)

    deploy_dir = op.join(ctx.envdir, "deploy")
    if not op.exists(deploy_dir):
        create = click.style("   create", fg="green")
        ctx.log("   " + create + "  " + deploy_dir)
        os.mkdir(deploy_dir)

    with open(
        op.join(deploy_dir, f"{ctx.composition_flavour_prefix}.json"), "w"
    ) as outfile:
        outfile.write(json_deployment)

    ctx.deployment_info = deployment
    return


def generate_kexec_scripts(ctx):
    if ctx.use_httpd:
        base_url = f"http://{ctx.httpd.ip}:{ctx.httpd.port}"
        deploy_info_src = f"{base_url}/deploy/{ctx.composition_flavour_prefix}.json"
    else:
        generate_deploy_info_b64(ctx)
        deploy_info_src = ctx.deployment_info_b64

    base_path = op.join(
        ctx.envdir, f"artifact/{ctx.composition_name}/{ctx.flavour.name}"
    )
    kexec_scripts_path = op.join(base_path, "kexec_scripts")
    os.makedirs(kexec_scripts_path, mode=0o700, exist_ok=True)

    if "all" in ctx.deployment_info:
        kernel_path = realpath_from_store(ctx, ctx.deployment_info["all"]["kernel"])
        initrd_path = realpath_from_store(ctx, ctx.deployment_info["all"]["initrd"])

        kexec_args = "-l $KERNEL --initrd=$INITRD "
        kexec_args += f"--append='deploy={deploy_info_src} console=tty0 console=ttyS0,115200 $DEBUG_INITRD'"
        script_path = op.join(kexec_scripts_path, "kexec.sh")
        with open(script_path, "w") as kexec_script:
            kexec_script.write("#!/usr/bin/env bash\n")
            kexec_script.write(": ${SUDO:=sudo}\n")
            kexec_script.write(": ${KERNEL:=" + kernel_path + "}\n")
            kexec_script.write(": ${INITRD:=" + initrd_path + "}\n")
            kexec_script.write(f"$SUDO kexec {kexec_args}\n")
            kexec_script.write("$SUDO kexec -e\n")
        os.chmod(script_path, 0o755)
    else:
        for ip, v in ctx.deployment_info["deployment"].items():
            role = v["role"]
            kernel_path = f"{base_path}/kernel_{role}"
            initrd_path = f"{base_path}/initrd_{role}"
            init_path = v["init"]
            kexec_args = f"-l {kernel_path} --initrd={initrd_path} "
            kexec_args += f"--append='init={init_path} deploy={deploy_info_src} console=tty0 console=ttyS0,115200 $DEBUG_INITRD'"
            script_path = op.join(kexec_scripts_path, f"kexec_{role}.sh")
            with open(script_path, "w") as kexec_script:
                kexec_script.write("#!/usr/bin/env bash\n")
                kexec_script.write(": ''${SUDO:=sudo}\n")
                kexec_script.write(f"$SUDO kexec {kexec_args}\n")
                kexec_script.write("$SUDO kexec -e\n")

            os.chmod(script_path, 0o755)


def generate_deploy_info_b64(ctx):

    deployment_info = {
        k: ctx.deployment_info[k]
        for k in [n for n in ctx.deployment_info.keys() if n != "deployment"]
    }

    deployment = {
        k: {"role": v["role"], "host": v["host"] if "host" in v else v["role"]}
        for k, v in ctx.deployment_info["deployment"].items()
    }

    # TODO: function to add multiple hostnames with same role
    # deployment = { k: {"role": v["role"], "host": "yopXXX"} for k,v in ctx.deployment_info["deployment"].items()}

    deployment_info["deployment"] = deployment

    ctx.vlog(f"deploy info \n{deployment_info}")
    deployment_info_str = json.dumps(deployment_info)

    ctx.deployment_info_b64 = base64.b64encode(deployment_info_str.encode()).decode()

    if len(ctx.deployment_info_b64) > (4096 - 256):
        ctx.log(
            "The base64 encoded deploy data is too large: use an http server to serve it"
        )
        sys.exit(1)
    return


# def copy_result_from_store(ctx):

#     if not ctx.compose_info:
#         read_compose_info(ctx)

#     if ctx.multiple_compositions:
#         composition_directory = "::"
#     else:
#         composition_directory = ctx.composition_name

#     artifact_path = op.join(
#         ctx.envdir, f"artifact/{composition_directory}/{ctx.flavour_name}"
#     )
#     if not op.exists(artifact_path):
#         create = click.style("   create", fg="green")
#         ctx.log("   " + create + "  " + artifact_path)
#         pathlib.Path(artifact_path).mkdir(parents=True, exist_ok=True)

#     if ctx.multiple_compositions:
#         compose_info = ctx.compositions_info
#     else:
#         compose_info = ctx.compose_info

#     new_compose_info = compose_info.copy()

#     if "all" in compose_info:
#         for target in ["kernel", "initrd", "qemu_script", "image"]:
#             if target in compose_info["all"]:
#                 new_target = op.join(artifact_path, target)
#                 shutil.copy(compose_info["all"][target], new_target)
#                 os.chmod(new_target, 0o644)
#                 new_compose_info["all"][target] = new_target
#     else:
#         for r, v in compose_info["nodes"].items():
#             for target in ["kernel", "initrd", "qemu_script", "image"]:
#                 if target in v:
#                     new_target = op.join(artifact_path, f"{target}_{r}")
#                     shutil.copy(v[target], new_target)
#                     os.chmod(new_target, 0o644)
#                     new_compose_info["nodes"][target] = new_target

#     if "test_script" in compose_info:
#         new_target = op.join(artifact_path, "test_script")
#         shutil.copy(compose_info["test_script"], new_target)
#         os.chmod(new_target, 0o644)
#         new_compose_info["test_script"] = new_target

#     # save new updated compose_info
#     json_new_compose_info = json.dumps(new_compose_info, indent=2)
#     with open(
#         op.join(ctx.envdir, f"build/{ctx.composition_flavour_prefix}::artifact"), "w",
#     ) as outfile:
#         outfile.write(json_new_compose_info)

#     ctx.compose_info = new_compose_info
#     ctx.log("Copy from store: " + click.style("completed", fg="green"))


##
#  Operation: connect, launch, wait_ssh_port,
#


def launch_ssh_kexec(ctx, ip=None, debug=False):
    if "all" in ctx.deployment_info:
        if ctx.push_path:
            kexec_script = f"{ctx.push_path}/kexec.sh"
            ki = f"KERNEL={ctx.push_path}kernel INITRD={ctx.push_path}initrd"
            user = "root@"
        else:
            base_path = op.join(
                ctx.envdir, f"artifact/{ctx.composition_name}/{ctx.flavour.name}"
            )
            kexec_script = op.join(base_path, "kexec_scripts/kexec.sh")
            ki = ""
            user = ""
        if ctx.sudo:
            sudo = f"SUDO={ctx.sudo}"
        else:
            sudo = ""

        if "DEBUG_STAGE1" in os.environ or debug:
            # debug_stage1 = os.environ["DEBUG_STAGE1"]
            # TODO
            ctx.wlog(
                "Machine selection not yet supported within ssh_kexec. Debug_stage1 will apply to all."
            )
            ki = f" {ki} DEBUG_INITRD=boot.debug1mounts "

        def one_ssh_kexec(ip_addr):
            ssh_cmd = f'{ctx.ssh} {user}{ip_addr} "screen -dm bash -c \\"{sudo} {ki} {kexec_script}\\""'
            ctx.vlog(ssh_cmd)
            subprocess.call(ssh_cmd, shell=True)

        if ip:
            one_ssh_kexec(ip)
        else:
            for ip in ctx.deployment_info["deployment"].keys():
                one_ssh_kexec(ip)
    else:
        raise Exception("Sorry, only all-in-one image version is supported up to now")


def wait_ssh_ports(ctx, ips=None, halo=True):
    ctx.log("Waiting ssh ports:")
    if not ips:
        ips = ctx.ip_addresses
    nb_ips = len(ips)

    nb_ssh_port = 0
    waiting_ssh_ports_cmd = (
        f"nmap -p22 -Pn {' '.join(ips)} -oG - | grep '22/open' | wc -l"
    )
    ctx.vlog(waiting_ssh_ports_cmd)
    if halo:
        spinner = Halo(text=f"Opened ssh ports 0/{nb_ips}", spinner="dots")
        spinner.start()
    while nb_ssh_port != nb_ips:
        output = subprocess.check_output(waiting_ssh_ports_cmd, shell=True)
        nb_ssh_port = int(output.rstrip().decode())
        if halo:
            spinner.text = "Opened ssh ports: {}/{} ({:.1f}s)".format(
                nb_ssh_port, nb_ips, ctx.elapsed_time()
            )
        time.sleep(0.25)
    if halo:
        spinner.succeed("Deployment taken {:.1f} sec".format(ctx.elapsed_time()))
    else:
        ctx.vlog("Deployment took {:.1f}s".format(ctx.elapsed_time()))


def push_on_machines(ctx):
    if "all" not in ctx.deployment_info:
        raise Exception("Sorry, only all-in-one image version is supported up to now")

    kernel = realpath_from_store(ctx, ctx.deployment_info["all"]["kernel"])
    initrd = realpath_from_store(ctx, ctx.deployment_info["all"]["initrd"])

    base_path = ctx.envdir

    # if ctx.multiple_compositions:
    #    subpath = "::"
    # else:
    # subpath = ctx.composition_name
    # base_path = op.join(
    #     ctx.envdir, f"artifact/{subpath}/{ctx.flavour_name}"
    # )
    subpath = ctx.composition_name
    base_path = op.join(ctx.envdir, f"artifact/{subpath}/{ctx.flavour_name}")

    kexec_script = op.join(base_path, "kexec_scripts/kexec.sh")

    ctx.vlog(
        f"push kernel, initrd, kexec_script on {ctx.ip_addresses} with scp executed concurrently"
    )
    for file_input in [kernel, initrd, kexec_script]:
        ctx.vlog(f"push: {file_input}")
        tasks_cmd = generate_scp_tasks(
            ctx.ip_addresses, file_input, ctx.push_path, scp="scp", user="root"
        )
        exec_kataract_tasks(tasks_cmd, elog=ctx.elog, vlog=ctx.vlog)

    # if shutil.which("kastafior"):
    #    raise NotImplementedError
    # elif shutil.which("kaput"):
    #     ctx.vlog("push kernel, initrd, kexec_script on hosts with kaput")
    #     joined_ip_addresses = ",".join(ctx.ip_addresses)
    #     for f in [kernel, initrd, kexec_script]:
    #         kaput_cmd = f"kaput -l root -n {joined_ip_addresses} {f} {ctx.push_path}"
    #         ctx.vlog(kaput_cmd)
    #         subprocess.call(kaput_cmd, shell=True)
    # else:
    #     for ip_address in ctx.ip_addresses:
    #         ctx.vlog(f"push kernel, initrd, kexec_script to {ip_address}")
    #         for f in [kernel, initrd, kexec_script]:
    #             subprocess.call(
    #                 f"scp {f} root@{ip_address}:{ctx.push_path}", shell=True
    #             )


def ssh_connect(ctx, user, node, execute=True):
    if not ctx.deployment_info:
        read_deployment_info(ctx)
    role = node
    host = node
    ssh_port = None
    for ip, v in ctx.deployment_info["deployment"].items():
        # if v["role"] == role:
        if v["host"] == role:
            host = ip
            if "ssh-port" in v:
                ssh_port = v["ssh-port"]
            break

    ssh_cmd = f"ssh -o StrictHostKeyChecking=no -o LogLevel=ERROR -l {user} "
    if ssh_port:
        ssh_cmd += f"-p {ssh_port} {host}"
    else:
        ssh_cmd += f"{host}"

    if execute:
        return_code = subprocess.run(ssh_cmd, shell=True).returncode

        if return_code:
            ctx.wlog(f"SSH exit code is not null: {return_code}")
        sys.exit(return_code)
    else:
        return ssh_cmd


NB_PANES_2_GEOMETRY = ["1", "1+1", "1+2", "2+2", "2+3", "3+3", "3+4", "4+4"]


def connect_tmux(ctx, user, nodes, pane_console, geometry, window_name="nxc"):
    if not nodes:
        deploy = ctx.deployment_info["deployment"]
        node = (list(deploy.keys()))[0]
        try:
            ipaddress.ip_address(node)
            nodes = [v["host"] for v in deploy.values()]
        except ValueError:
            nodes = list(deploy.keys())

    ssh_cmds = [ctx.flavour.ext_connect(user, node, execute=False) for node in nodes]

    console = 0
    if pane_console:
        console = 1

    if not geometry:
        geometry = ""
        nb_panes = len(nodes)
        if pane_console:
            nb_panes += 1

        if nb_panes < 9:
            geometry = NB_PANES_2_GEOMETRY[nb_panes - 1]

    # translate geometry
    if "+" in geometry and "*" in geometry:
        raise Exception("Mixing + and * in geometry is not supported")
    if "+" in geometry:
        splitw = [int(i) for i in geometry.split("+")]
        splitw.reverse()
    elif "*" in geometry:
        g = geometry.split("*")
        splitw = [int(g[1]) for i in range(int(g[0]))]
    else:
        splitw = [int(geometry)]

    nb_panes = sum(splitw)
    nb_splitv = len(splitw)

    ctx.vlog(f"geometry: {geometry}")
    ctx.vlog(f"splitw: {splitw}")
    ctx.vlog(f"nb_panes: {nb_panes}")

    # prepare commands
    base_cmds = ["bash" for i in range(nb_panes)]

    nb_ssh_cmds = len(ssh_cmds)
    if (nb_ssh_cmds + console) > nb_panes:
        ssh_cmds = ssh_cmds[: nb_panes - console]
    cmds = base_cmds[: nb_panes - nb_ssh_cmds] + ssh_cmds

    ctx.vlog(f"cmds: {cmds}")

    cmds.reverse()

    if "TMUX" not in os.environ:
        cmd = "tmux new -d"
        subprocess.call(cmd, shell=True)

    i = 0

    cmd = f"tmux new-window -n {window_name} -d"
    if not pane_console:
        cmd += f" {cmds[i]}"
        i += 1
    subprocess.call(cmd, shell=True)

    # cmd = f'tmux splitw -h -p 50 -t {window_name}.0 "{ssh_cmds[0]}"'
    # subprocess.call(cmd, shell=True)

    for v in range(nb_splitv):
        ratio_h = round(100.0 / (nb_splitv - v))
        if ratio_h != 100:
            cmd = f"tmux splitw -h -p {ratio_h} -t {window_name}.0 {cmds[i]}"
            pane0 = 1
            # print("vertical |", cmd)
            # print(round(100 * (1.0 / (nb_splitv - v))))
            subprocess.call(cmd, shell=True)
            i += 1
        else:
            pane0 = 0

        for h in range(splitw[v] - 1):
            ratio_v = round(100 * (1 - (1.0 / (splitw[v] - h))))
            cmd = f"tmux splitw -v -p {ratio_v} -t {window_name}.{h+pane0} {cmds[i]}"
            # print(cmd)
            subprocess.call(cmd, shell=True)
            i += 1

    cmd = f"tmux select-pane -t {window_name}.0"
    subprocess.call(cmd, shell=True)

    cmd = f"tmux select-window -t {window_name}"
    subprocess.call(cmd, shell=True)

    if "TMUX" not in os.environ:
        cmd = "tmux attach"
        subprocess.call(cmd, shell=True)


# TODO launch_vm(ctx, kexec_info, debug=False):
# TODO launch_vm_deploy(ctx, deployment, httpd_port=0, debug=False):
# NOT USED
def launch_vm(ctx, httpd_port=0, debug=False):

    if not op.exists("/tmp/kexec-qemu-vde1.ctl/ctl"):
        ctx.log("need sudo to create tap0 interface")
        subprocess.call("sudo true", shell=True)

    for ip, v in ctx.deployment_info["deployment"].items():
        qemu_script = v["qemu_script"]
        # cmd_qemu_script = f"DEPLOY='deploy=http://10.0.2.1:{httpd_port}/deployment.json' TAP=1"
        #
        cmd_qemu_script = "TAP=1"
        if httpd_port:
            cmd_qemu_script = " DEPLOY=1"
        if debug:
            cmd_qemu_script += " DEBUG_INITRD=boot.debug1mounts"

        cmd_qemu_script += " VM_ID={:02d} {} &".format(v["vm_id"], qemu_script)
        ctx.log("launch: {}".format(v["role"]))
        ctx.vlog(f"command: {cmd_qemu_script}")


# Helpers
def kill_proc_tree(
    pid, sig=signal.SIGTERM, include_parent=True, timeout=None, on_terminate=None
):
    """Kill a process tree (including grandchildren) with signal
    "sig" and return a (gone, still_alive) tuple.
    "on_terminate", if specified, is a callback function which is
    called as soon as a child terminates.
    From: https://psutil.readthedocs.io/en/latest/#kill-process-tree
    """
    assert pid != os.getpid(), "won't kill myself"
    parent = psutil.Process(pid)
    children = parent.children(recursive=True)
    if include_parent:
        children.append(parent)
    for p in children:
        try:
            p.send_signal(sig)
        except psutil.NoSuchProcess:
            pass
    gone, alive = psutil.wait_procs(children, timeout=timeout, callback=on_terminate)
    return (gone, alive)