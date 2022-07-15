{
  description = "nixos-compose";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/22.05";
    flake-utils.url = "github:numtide/flake-utils";
    kapack.url = "github:oar-team/nur-kapack";
    kapack.inputs.nixpkgs.follows = "nixpkgs";
  };

  outputs = { self, nixpkgs, flake-utils, kapack }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        python3pkgs = pkgs.python3Packages;
        kapackpkgs = kapack.packages.${system};

        #customOverrides = self: super: {
        # Overrides go here
        #};

        app = python3pkgs.buildPythonPackage rec {
          pname = "nxc";
          version = "locale";
          name = "${pname}-${version}";

          src = builtins.filterSource
            (path: type: type != "directory" || baseNameOf path != ".git" || path != "result")
            ./.;

          format = "pyproject";
          buildInputs = [ python3pkgs.poetry ];
          propagatedBuildInputs = with python3pkgs; [
            click
            kapackpkgs.execo
            halo
            pexpect
            psutil
            ptpython
            pyinotify
            pyyaml
            requests
            tomlkit
          ] ++ [ pkgs.taktuk ];
        };

        packageName = "nixos-compose";
      in rec {
        packages = {
          ${packageName} = app;
          # "${packageName}-full" = app.overrideAttrs(attr: rec {
          #   propagatedBuildInputs = attr.propagatedBuildInputs ++ [
          #     pkgs.docker-compose
          #     pkgs.qemu_kvm
          #     pkgs.vde2
          #   ];
          # });
          showTemplates = pkgs.writeText "templates.json" (
            builtins.toJSON (builtins.mapAttrs (name: value: value.description) self.templates)
          );
        };

        defaultPackage = self.packages.${system}.${packageName};

        devShells = {
          nxcShell = pkgs.mkShell {
            buildInputs = [ self.defaultPackage.${system} ];
          };
          nxcShellFull = pkgs.mkShell {
            buildInputs = [
              self.packages.${system}.${packageName}
              pkgs.docker-compose
              pkgs.qemu_kvm
              pkgs.vde2
              pkgs.tmux
            ];
          };
        };

        devShell = pkgs.mkShell {
          buildInputs = with pkgs; [ poetry ];
          inputsFrom = builtins.attrValues self.packages.${system};
        };

    }) //
  {lib = import ./nix/lib.nix; templates = import ./examples/nix_flake_templates.nix; overlay = import ./overlay.nix { inherit self; };};
}
