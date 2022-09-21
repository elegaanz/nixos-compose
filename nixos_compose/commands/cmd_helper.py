import click
from ..context import pass_context
from ..g5k import key_sleep_script


def print_helper(ctx, options):
    for option in options:
        if option == "g5k_script":
            click.echo(key_sleep_script)
        else:
            ctx.elog(f"Helper: {option} does not exist")


def print_helper_list(helper_options):
    print("g5k_script: print path to g5k_key_sleep_script Grid'5000 script")


@click.command("helper")
@click.option(
    "-l",
    "--list",
    # "--list-helpers",
    is_flag=True,
    help="List of available helpers",
)
@pass_context
@click.argument("options", nargs=-1)
def cli(ctx, options, list):
    """Specific and contextual helper information (e.g. g5k_script path for Grid'5000)
    Warning: Experimental command, may be removed in the future or change without backward compatibility care."""
    if options:
        print_helper(ctx, options)

    if list:
        print_helper_list(ctx)