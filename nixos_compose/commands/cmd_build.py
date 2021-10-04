import os
import os.path as op
import sys
import subprocess
import click
import json

from ..context import pass_context, on_started, on_finished

# FLAVOURS_PATH = op.abspath(op.join(op.dirname(__file__), "../", "flavours"))
# FLAVOURS = os.listdir(FLAVOURS_PATH)


@click.command("build")
@click.argument(
    "composition_file", required=False, type=click.Path(exists=True, resolve_path=True)
)
@click.option(
    "--nix-path",
    "-I",
    multiple=True,
    help="add a path to the list of locations used to look up <...> file names",
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
@click.option(
    "--legacy-nix", "-l", is_flag=True, help="Use legacy Nix's CLI.",
)
@click.option("--show-trace", is_flag=True, help="Show Nix trace")
@click.option(
    "--dry-run", is_flag=True, help="Show what this command would do without doing it"
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
@pass_context
@on_finished(lambda ctx: ctx.show_elapsed_time())
@on_started(lambda ctx: ctx.assert_valid_env())
def cli(
    ctx,
    composition_file,
    nix_path,
    nix_flags,
    out_link,
    nixpkgs,
    flavour,
    list_flavours,
    legacy_nix,
    show_trace,
    dry_run,
    composition_flavour,
    list_compositions_flavours,
):
    """Build multi Nixos composition.
    Typically it performs the kind of following command:
      nix build -f examples/webserver-flavour.nix -I compose=nix/compose.nix -I nixpkgs=channel:nixos-20.09 -o result-local
    """

    build_cmd = ""

    description_flavours_file = op.abspath(op.join(ctx.envdir, "nix/flavours.json"))
    description_flavours = json.load(open(description_flavours_file, "r"))

    flavours = [k for k in description_flavours.keys()]

    if list_flavours:
        ctx.log("Flavours List:")
        for k in flavours:
            click.echo(f"{k: <18}: {description_flavours[k]['description']}")
        sys.exit(0)

    # Do we are in flake context
    flake = True if op.exists(op.join(ctx.envdir, "flake.nix")) else False

    if not flavour and not flake:
        if ctx.platform:
            flavour = ctx.platform.default_flavour
            click.echo(
                f"Platform's default flavour setting: {click.style(flavour, fg='green')}"
            )
        else:
            flavour = "nixos-test"

    # import pdb; pdb.set_trace()
    flavour_arg = ""
    if flavour:
        if flavour not in flavours and not op.isfile(flavour):
            raise click.ClickException(
                f'"{flavour}" is neither a supported flavour nor flavour_path'
            )
        else:
            if flavour in flavours:
                flavour_arg = f" --argstr flavour {flavour}"
            else:
                flavour_arg = f" --arg flavour {op.abspath(flavour)}"

    if not composition_file:
        composition_file = ctx.nxc["composition"]

    # TODO remove, we'll use default.nix
    # compose_file = op.join(ctx.envdir, "nix/compose.nix")

    # if out_link == "result":
    #    out_link = op.join(ctx.envdir, out_link)

    nix_flake_support = False
    if not subprocess.call(
        "nix flake --help",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=True,
    ):
        nix_flake_support = True

    if list_compositions_flavours:
        default_nix = op.join(ctx.envdir, "default.nix")
        if not flake and op.exists(default_nix):
            ctx.elog("flake.nix with default.nix must be provided for this option.")
            sys.exit(1)
        else:
            cmd = ["nix", "search", "-f", default_nix, "--json"]
            raw_compositions_flavours = json.loads(
                subprocess.check_output(cmd).decode()
            )
            for n, k in raw_compositions_flavours.items():
                if n == "default":
                    print(click.style("Default", fg="green") + ": " + k["pname"])
                else:
                    print(n.split(".")[2])
            sys.exit(1)

    if composition_flavour and not flake:
        ctx.elog("flake.nix with default.nix must be provided for this option.")
        sys.exit(1)

    if show_trace:
        build_cmd += " --show-trace"

    if nixpkgs:
        build_cmd += f" -I nixpkgs={nixpkgs}"

    if flavour_arg and not flake:
        build_cmd += f" {flavour_arg}"

    #
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

    build_cmd += f" -o {out_link}"

    if flake:
        if not composition_flavour and flavour:
            composition_flavour = f"composition::{flavour}"
        if flavour:
            if nix_flake_support and not legacy_nix:
                build_cmd = f'nix build {build_cmd} ".#packages.x86_64-linux.{composition_flavour}"'
            else:
                build_cmd = f"nix-build {build_cmd} -A packages.x86_64-linux.{composition_flavour}"
    else:
        if legacy_nix:
            build_cmd = f"nix-build {build_cmd}"
        else:
            build_cmd = f"nix build {build_cmd}"

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
        if flavour == "docker":
            with open(out_link, "r") as compose_info_json:
                content = json.load(compose_info_json)
                docker_image = content["image"]
                docker_load_command = f"docker load < {docker_image}"
                returncode = subprocess.call(docker_load_command, shell=True)
                if returncode:
                    ctx.elog(f"Build return code: {returncode}")
                    sys.exit(returncode)
            ctx.glog("Docker Image loaded")

        ctx.glog("Build completed")
    else:
        ctx.log("Dry-run:")
        ctx.log(f"   working directory:          {ctx.envdir}")
        ctx.log(f"   composition flavour prefix: {ctx.composition_flavour_prefix}")
        ctx.log(f"   build command:              {build_cmd}")
