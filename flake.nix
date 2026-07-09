{
  description = "Jacques — automatic disc ripping daemon with web UI";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    let
      # NixOS module is declared outside eachSystem because it is system-independent.
      # It resolves self.packages.${pkgs.system} at module evaluation time, when pkgs
      # (and therefore pkgs.system) is already known.
      nixosModule = { config, lib, pkgs, ... }:
        let
          cfg = config.services.jacques;
          pkg = self.packages.${pkgs.system}.default;
        in
        {
          options.services.jacques = {
            enable = lib.mkEnableOption "Jacques automatic disc ripping daemon";

            outputPath = lib.mkOption {
              type = lib.types.str;
              default = "/var/lib/jacques/library";
              description = "Directory where ripped and organized media is stored.";
            };

            tempPath = lib.mkOption {
              type = lib.types.str;
              default = "/var/lib/jacques/tmp";
              description = "Temporary working directory for in-progress ripping and transcoding.";
            };

            port = lib.mkOption {
              type = lib.types.port;
              default = 8080;
              description = ''
                Port for the web UI. The UI has no authentication — bind to
                localhost or place behind a reverse proxy for non-local access.
              '';
            };

            handbrakeQuality = lib.mkOption {
              type = lib.types.ints.between 0 51;
              default = 20;
              description = "HandBrake RF quality factor. Lower = better quality. 18–22 recommended for H.265.";
            };

            tmdbApiKeyFile = lib.mkOption {
              type = lib.types.nullOr lib.types.path;
              default = null;
              example = "/run/secrets/tmdb-api-key";
              description = ''
                Path to a file containing the TMDb API key in the form:
                  JACQUES_TMDB_API_KEY=<key>
                Use a secrets manager such as agenix or sops-nix to provision this file.
              '';
            };

            user = lib.mkOption {
              type = lib.types.str;
              default = "jacques";
              description = "User account that runs the daemon. Created automatically when using the default name.";
            };

            group = lib.mkOption {
              type = lib.types.str;
              default = "jacques";
              description = "Primary group for the daemon. Created automatically when using the default name.";
            };

            openFirewall = lib.mkOption {
              type = lib.types.bool;
              default = false;
              description = "Open the web UI port in the NixOS firewall.";
            };
          };

          config = lib.mkIf cfg.enable {
            systemd.services.jacques = {
              description = "Jacques disc ripping daemon";
              # Wait for udev to finish settling so optical drives are enumerated
              # before the daemon's pyudev monitor starts.
              after = [ "network.target" "systemd-udev-settle.service" ];
              wantedBy = [ "multi-user.target" ];

              environment = {
                JACQUES_OUTPUT_PATH = cfg.outputPath;
                JACQUES_TEMP_PATH = cfg.tempPath;
                JACQUES_PORT = toString cfg.port;
                JACQUES_HANDBRAKE_QUALITY = toString cfg.handbrakeQuality;
                # Store the database alongside state, not in tempPath.
                JACQUES_DB_PATH = "/var/lib/jacques/jacques.db";
              };

              serviceConfig = {
                ExecStart = "${pkg}/bin/jacques";
                Restart = "on-failure";
                RestartSec = "5s";

                User = cfg.user;
                Group = cfg.group;
                # cdrom group is required to read from /dev/srN devices.
                SupplementaryGroups = "cdrom";

                # systemd manages /var/lib/jacques with correct ownership.
                StateDirectory = "jacques";
                StateDirectoryMode = "0750";

                EnvironmentFile = lib.optional (cfg.tmdbApiKeyFile != null) cfg.tmdbApiKeyFile;

                # Hardening — ProtectSystem=strict + ReadWritePaths covers
                # everything the daemon legitimately writes.
                NoNewPrivileges = true;
                ProtectSystem = "strict";
                ProtectHome = true;
                PrivateTmp = true;
                ReadWritePaths = [
                  cfg.outputPath
                  cfg.tempPath
                  "/var/lib/jacques"
                ];
              };

              # Ensure output and temp directories exist before starting.
              preStart = ''
                mkdir -p ${lib.escapeShellArg cfg.outputPath}
                mkdir -p ${lib.escapeShellArg cfg.tempPath}
              '';
            };

            # Only create the user/group when using the default names; a custom
            # user implies the admin has already created the account.
            users.users = lib.mkIf (cfg.user == "jacques") {
              jacques = {
                isSystemUser = true;
                group = cfg.group;
                extraGroups = [ "cdrom" ];
                description = "Jacques disc ripping daemon";
              };
            };

            users.groups = lib.mkIf (cfg.group == "jacques") {
              jacques = { };
            };

            networking.firewall.allowedTCPPorts = lib.optional cfg.openFirewall cfg.port;
          };
        };
    in
    {
      nixosModules.default = nixosModule;
      nixosModules.jacques = nixosModule;
    }
    // flake-utils.lib.eachSystem [ "x86_64-linux" "aarch64-linux" ] (system:
      let
        pkgs = import nixpkgs {
          inherit system;
          # MakeMKV is proprietary (unfree). This only affects this local pkgs
          # instantiation, not the caller's nixpkgs.
          config.allowUnfree = true;
        };

        python = pkgs.python312;

        wordsegment = python.pkgs.buildPythonPackage rec {
          pname = "wordsegment";
          version = "1.3.1";
          pyproject = true;
          build-system = [ python.pkgs.setuptools ];
          src = pkgs.fetchPypi {
            inherit pname version;
            sha256 = "3dcc7cd1e9bba3f3ffe6a0e54d98377bc502fc34e9e9d8c8199ac5636924f023";
          };
          doCheck = false;
        };

        # All runtime Python dependencies available in nixpkgs.
        # Hyphenated attribute names require quoted access.
        runtimeDeps = [
          python.pkgs.fastapi
          python.pkgs.uvicorn
          python.pkgs.jinja2
          python.pkgs."python-multipart"
          python.pkgs.sqlalchemy
          python.pkgs.aiosqlite
          python.pkgs."pydantic-settings"
          python.pkgs.pyudev
          python.pkgs.httpx
          python.pkgs.rich
          wordsegment
        ];

        jacquesApp = python.pkgs.buildPythonApplication {
          pname = "jacques";
          version = "0.1.0";
          pyproject = true;
          src = ./.;

          build-system = [ python.pkgs.setuptools ];

          dependencies = runtimeDeps;

          nativeBuildInputs = [ pkgs.makeWrapper ];

          # Bake the external binary paths into the wrapper so makemkvcon and
          # HandBrakeCLI are always available, independent of the system PATH.
          postInstall = ''
            wrapProgram $out/bin/jacques \
              --prefix PATH : ${pkgs.lib.makeBinPath [
                pkgs.makemkv   # provides makemkvcon
                pkgs.handbrake # provides HandBrakeCLI
              ]}
          '';

          # Unit tests pass without hardware; the subset that call makemkvcon /
          # HandBrakeCLI are integration tests that mock those subprocesses.
          # Full test run: uv run pytest
          doCheck = false;

          meta = with pkgs.lib; {
            description = "Automatic disc ripping daemon with web UI";
            homepage = "https://github.com/thefirstofthe300/jacques";
            license = licenses.mit;
            maintainers = [ ];
            platforms = platforms.linux;
          };
        };
      in
      {
        packages = {
          default = jacquesApp;
          jacques = jacquesApp;
        };

        devShells.default = pkgs.mkShell {
          packages = [
            pkgs.uv
            pkgs.makemkv
            pkgs.handbrake
          ];

          shellHook = ''
            export LD_LIBRARY_PATH="${pkgs.stdenv.cc.cc.lib}/lib:$LD_LIBRARY_PATH"
            echo "Jacques dev shell"
            printf "  uv:           %s\n" "$(uv --version 2>/dev/null || echo 'not found')"
            printf "  makemkvcon:   %s\n" "$(command -v makemkvcon  2>/dev/null || echo 'not found')"
            printf "  HandBrakeCLI: %s\n" "$(command -v HandBrakeCLI 2>/dev/null || echo 'not found')"
            echo ""
            echo "Run 'uv sync --group dev' to set up the virtual environment."
          '';
        };
      });
}
