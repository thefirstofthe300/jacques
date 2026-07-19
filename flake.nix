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
          python.pkgs."python-multipart"
          python.pkgs.sqlalchemy
          python.pkgs.aiosqlite
          python.pkgs."pydantic-settings"
          python.pkgs.pyudev
          python.pkgs.httpx
          python.pkgs.rich
          python.pkgs.pycdlib
          wordsegment
        ];

        # Svelte + Vite SPA that replaced the old Jinja2/HTMX dashboard.
        # `jacques/api/app.py` serves it from a `static/` directory sibling to
        # the installed package's Python files, so it has to be built and
        # baked into jacquesApp below.
        #
        # frontend/vite.config.js sets `build.outDir` to `../jacques/static`
        # -- relative to frontend/'s cwd during `vite build`, which escapes
        # the frontend/ directory entirely and lands at a `jacques/static`
        # directory *sibling* to frontend/ (i.e. the repo root's jacques/
        # package directory). buildNpmPackage normally expects the build's
        # output inside the package directory itself (e.g. dist/), so pulling
        # in only `./frontend` as src would leave that `../jacques` path
        # resolving to nothing but bare scratch space outside the checkout.
        # Instead, pull in the whole repo as src and build from the
        # frontend/ subdirectory (via sourceRoot) so the escaping relative
        # path resolves to a real `jacques/static` inside the same copied
        # source tree -- which is exactly what installPhase then copies out
        # of. This only touches the isolated build's copy of the repo, never
        # the real working tree.
        jacquesFrontend = pkgs.buildNpmPackage {
          pname = "jacques-frontend";
          version = "0.1.0";
          # The default unpackPhase copies a directory `src` into the build
          # top under a name derived from its store path's own "name"
          # component (via `stripHash`). A bare `./.` gets an unpredictable
          # store-path name, so pin it explicitly with `builtins.path` --
          # that makes `sourceRoot` below (which must name that directory
          # exactly to cd into frontend/) stable and known ahead of time.
          # This same `src`/`sourceRoot` pair is also handed to
          # buildNpmPackage's internal fixed-output derivation that
          # prefetches npm deps for npmDepsHash, which only forwards `src`
          # and `sourceRoot` (not arbitrary phases) -- so it must resolve
          # correctly through the default unpackPhase alone, with no custom
          # unpackPhase override.
          src = builtins.path {
            path = ./.;
            name = "jacques-source";
          };
          sourceRoot = "jacques-source/frontend";

          # Computed from frontend/package-lock.json; update by running
          # `nix build .#jacques`, which fails with the actual hash to paste
          # in whenever package-lock.json changes.
          npmDepsHash = "sha256-sRPYmja4HXQDHv/Y6IWbFDDVmMZQkT3UjE/1+nTRghY=";

          # unpackPhase only chmods sourceRoot (jacques-source/frontend)
          # writable; the escaped outDir's sibling `jacques/static/` is still
          # a read-only copy from the store (it only contains a tracked
          # `.gitkeep`), and Vite's `emptyOutDir` needs to delete it before
          # writing the build there.
          postPatch = ''
            chmod -R u+w ../jacques/static
          '';

          installPhase = ''
            runHook preInstall
            cp -r ../jacques/static $out
            runHook postInstall
          '';
        };

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
          # Also bake the built frontend into the installed package's
          # static/ directory, replacing the empty/placeholder one that ships
          # in the sdist, so app.py's StaticFiles mount serves real assets.
          postInstall = ''
            wrapProgram $out/bin/jacques \
              --prefix PATH : ${pkgs.lib.makeBinPath [
                pkgs.makemkv   # provides makemkvcon
                pkgs.handbrake # provides HandBrakeCLI
              ]}

            rm -rf $out/${python.sitePackages}/jacques/static
            cp -r ${jacquesFrontend} $out/${python.sitePackages}/jacques/static
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

        # `uv run pytest` needs libstdc++ for greenlet's compiled extension
        # (SQLAlchemy's async engine dependency). Nix-built Python binaries
        # never search system library paths — only RPATH/LD_LIBRARY_PATH — so
        # this fails whenever pytest runs outside an actual `nix develop`
        # session (the devShell's shellHook below sets it, but that only
        # covers interactive/`-c` shell entry). This app wraps the same fix so
        # tests can be run reliably from anywhere, e.g. CI or a script, via
        # `nix run .#test -- tests/test_pipeline.py -v`.
        apps.test = {
          type = "app";
          program = toString (pkgs.writeShellScript "jacques-run-tests" ''
            export LD_LIBRARY_PATH="${pkgs.stdenv.cc.cc.lib}/lib:$LD_LIBRARY_PATH"
            exec ${pkgs.uv}/bin/uv run pytest "$@"
          '');
        };

        devShells.default = pkgs.mkShell {
          packages = [
            pkgs.uv
            pkgs.makemkv
            pkgs.handbrake
            pkgs.nodejs # npm is bundled with nodejs in nixpkgs
          ];

          shellHook = ''
            export LD_LIBRARY_PATH="${pkgs.stdenv.cc.cc.lib}/lib:$LD_LIBRARY_PATH"
            echo "Jacques dev shell"
            printf "  uv:           %s\n" "$(uv --version 2>/dev/null || echo 'not found')"
            printf "  makemkvcon:   %s\n" "$(command -v makemkvcon  2>/dev/null || echo 'not found')"
            printf "  HandBrakeCLI: %s\n" "$(command -v HandBrakeCLI 2>/dev/null || echo 'not found')"
            printf "  node:         %s\n" "$(node --version 2>/dev/null || echo 'not found')"
            printf "  npm:          %s\n" "$(npm --version 2>/dev/null || echo 'not found')"
            echo ""
            echo "Run 'uv sync --group dev' to set up the virtual environment."
            echo "Run 'cd frontend && npm install' to set up frontend tooling."
          '';
        };
      });
}
