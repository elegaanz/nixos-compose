import os
import os.path as op
import sys
import subprocess
import click
import json

from ..context import pass_context, on_started, on_finished
from ..setup import apply_setup

# FLAVOURS_PATH = op.abspath(op.join(op.dirname(__file__), "../", "flavours"))
# FLAVOURS = os.listdir(FLAVOURS_PATH)


@click.command("build")
@click.argument(
    "composition_file", required=False, type=click.Path(exists=True, resolve_path=True)
)
@click.option(
    "--nix-flags",
    type=click.STRING,
    help='add nix flags (aka options) to nix build command, --nix-flags "--impure"',
)
@click.option("--out-link", "-o", help="path of the symlink to the build result")
@click.option("--nixpkgs", "-n", help="set <nixpkgs> ex: channel:nixos-20.09")
@click.option(
    "-f", "--flavour", type=click.STRING, help="Use particular flavour (name or path)",
)
@click.option(
    "-F", "--list-flavours", is_flag=True, help="List available flavour",
)
# TOREMOVE
# @click.option(
#    "--copy-from-store",
#    "-c",
#    is_flag=True,
#    help="Copy artifacts (initrd, kernels, ...) from Nix store to artifact directory",
# )
@click.option("--show-trace", is_flag=True, help="Show Nix trace")
@click.option(
    "--dry-run", is_flag=True, help="Show what this command would do without doing it"
)
@click.option(
    "--dry-build",
    is_flag=True,
    help="Eval build expression and show store entry without building derivation",
)
@click.option(
    "-C",
    "--composition-flavour",
    type=click.STRING,
    help="Use to specify which composition and flavour combinaison to built when muliple compostions are describe at once (see -L options to list them).",
)
# @click.option(
#    "-c", "--composition", type=click.STRING,
#    help="Use to specify which composition to built when muliple compostions are describe at once."
# )
@click.option(
    "-L",
    "--list-compositions-flavours",
    is_flag=True,
    help="List available combinaisons of compositions and flavours",
)
@click.option(
    "-s", "--setup", type=click.STRING, help="Select setup variant",
)
@click.option(
    "-p",
    "--setup-param",
    type=click.STRING,
    multiple=True,
    help="Override setup parameter",
)
@pass_context
@on_finished(lambda ctx: ctx.show_elapsed_time())
@on_started(lambda ctx: ctx.assert_valid_env())
def cli(
    ctx,
    composition_file,
    nix_flags,
    out_link,
    nixpkgs,
    flavour,
    list_flavours,
    show_trace,
    dry_run,
    dry_build,
    composition_flavour,
    list_compositions_flavours,
    setup,
    setup_param,
):
    """Build multi Nixos composition.
    Typically it performs the kind of following command:
      nix build -f examples/webserver-flavour.nix -I compose=nix/compose.nix -I nixpkgs=channel:nixos-20.09 -o result-local
    """

    if setup or op.exists(op.join(ctx.envdir, "setup.toml")):
        nix_flags, composition_file, composition_flavour, flavour = apply_setup(
            ctx,
            setup,
            nix_flags,
            composition_file,
            composition_flavour,
            flavour,
            setup_param,
        )

    build_cmd = ""

    # Do we are in flake context
    if not op.exists(op.join(ctx.envdir, "flake.nix")):
        ctx.elog("Not Found flake.nix file")
        sys.exit(1)

    description_flavours = get_flavours()

    flavours = list(description_flavours.keys())

    if list_flavours:
        ctx.log("Flavours List:")
        for k in flavours:
            click.echo(f"{k: <18}: {description_flavours[k]['description']}")
        sys.exit(0)

    # if not flavour and not flake:
    #     if ctx.platform:
    #         flavour = ctx.platform.default_flavour
    #         click.echo(
    #             f"Platform's default flavour setting: {click.style(flavour, fg='green')}"
    #         )
    #     else:
    #         flavour = "nixos-test
    #        "

    # import pdb; pdb.set_trace()

    if flavour:
        if flavour not in flavours and not op.isfile(flavour):
            ctx.elog(f'"{flavour}" is neither a supported flavour nor flavour_path')
            sys.exit(1)

    if not composition_file:
        composition_file = ctx.nxc["composition"]

    # TODO remove, we'll use default.nix
    # compose_file = op.join(ctx.envdir, "nix/compose.nix")

    # if out_link == "result":
    #    out_link = op.join(ctx.envdir, out_link)

    if subprocess.call(
        "nix flake --help",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=True,
    ):
        ctx.elog("Nix flakes must be enabled")
        sys.exit(1)

    if list_compositions_flavours:
        cmd = ["nix", "flake", "show", "--json"]
        raw_compositions_flavours = json.loads(subprocess.check_output(cmd).decode())
        for compo_flavour in filter(
            lambda x: x not in ["flavoursJson", "showFlavours"],
            raw_compositions_flavours["packages"]["x86_64-linux"].keys(),
        ):
            print(compo_flavour)
        print(
            click.style("Default", fg="green")
            + ": "
            + raw_compositions_flavours["defaultPackage"]["x86_64-linux"]["name"]
        )
        sys.exit(0)

    if show_trace:
        build_cmd += " --show-trace"

    if nixpkgs:
        build_cmd += f" -I nixpkgs={nixpkgs}"

    if not out_link:
        build_path = op.join(ctx.envdir, "build")
        if not op.exists(build_path):
            create = click.style("   create", fg="green")
            ctx.log("   " + create + "  " + build_path)
            os.mkdir(build_path)

        if not flavour:
            if "default_flavour" in ctx.nxc:
                flavour = ctx.nxc["default_flavour"]
            else:
                flavour = "default"

        if composition_flavour:
            ctx.composition_flavour_prefix = composition_flavour
            ctx.flavour_name = composition_flavour.split("::")[-1]
        else:
            composition_name = (os.path.basename(composition_file)).split(".")[0]
            ctx.composition_name = composition_name
            ctx.flavour_name = flavour
            ctx.composition_flavour_prefix = f"{composition_name}::{flavour}"

        out_link = op.join(build_path, ctx.composition_flavour_prefix)

    if dry_build:
        build_cmd = f"nix eval {build_cmd} --raw"
    else:
        build_cmd = f"nix build {build_cmd}"
        if out_link:
            build_cmd += f" -o {out_link}"

    if not composition_flavour and flavour:
        composition_flavour = f"composition::{flavour}"
    if flavour:
        build_cmd = f'{build_cmd} ".#packages.x86_64-linux.{composition_flavour}"'

    # add additional nix flags if any
    if nix_flags:
        build_cmd += " " + nix_flags
    # else:
    # TODO remove legacy_nix and use default.nix -> build_cmd += "-I composition={composition_file}"
    #    if not legacy_nix:
    # build_cmd += " -f"
    # build_cmd += f" {compose_file} -I composition={composition_file}"

    if not dry_run:
        ctx.glog("Starting Build")
        ctx.vlog(build_cmd)
        returncode = subprocess.call(build_cmd, cwd=ctx.envdir, shell=True)
        if returncode:
            ctx.elog(f"Build return code: {returncode}")
            sys.exit(returncode)

        # Loading the docker image"
        if flavour == "docker" and not dry_build:
            with open(out_link, "r") as compose_info_json:
                content = json.load(compose_info_json)
                docker_image = content["image"]
                docker_load_command = f"docker load < {docker_image}"
                returncode = subprocess.call(docker_load_command, shell=True)
                if returncode:
                    ctx.elog(f"Build return code: {returncode}")
                    sys.exit(returncode)
            ctx.glog("Docker Image loaded")

        ctx.glog("\nBuild completed")
    else:
        ctx.log("Dry-run:")
        ctx.log(f"   working directory:          {ctx.envdir}")
        ctx.log(f"   composition flavour prefix: {ctx.composition_flavour_prefix}")
        ctx.log(f"   build command:              {build_cmd}")


def get_flavours():
    """
    Returns the json representation of the available flavours
    """
    FLAVOURS_JSON = op.abspath(
        op.join(op.dirname(__file__), "../../nix", "flavours.json")
    )
    # import pdb; pdb.set_trace()
    flake_location = "."
    output_json = "/tmp/.flavours.json"
    retcode = subprocess.call(
        f"nix build {flake_location}#flavoursJson -o {output_json}",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=True,
    )
    if retcode:
        output_json = FLAVOURS_JSON
    return json.load(open(output_json, "r"))