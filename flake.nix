{
  description = "nixos-compose";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};

        #customOverrides = self: super: {
        # Overrides go here
        #};

        app = pkgs.poetry2nix.mkPoetryApplication {
          projectDir = ./.;
          #overrides =
          #  [ pkgs.poetry2nix.defaultPoetryOverrides customOverrides ];
          propagatedBuildInputs = [ pkgs.openssh ];
        };

        packageName = "nixos-compose";
      in rec {
        packages = {
          ${packageName} = app;
          showTemplates = pkgs.writeText "templates.json" (
            builtins.toJSON (builtins.mapAttrs (name: value: value.description) self.templates)
          );
        };

        defaultPackage = self.packages.${system}.${packageName};

        devShell = pkgs.mkShell {
          buildInputs = with pkgs; [ poetry ];
          inputsFrom = builtins.attrValues self.packages.${system};
        };

    }) //
  {lib = import ./nix/lib.nix; templates = import ./examples/nix_flake_templates.nix;};
}
