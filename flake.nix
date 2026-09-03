{
  description = "Meme-Kalender: ein deutsches Meme pro Arbeitstag";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
        python = pkgs.python3;

        runtimeDeps = ps: with ps; [
          fastapi
          uvicorn
          jinja2
          sqlalchemy
          httpx
          argon2-cffi
          itsdangerous
          pydantic-settings
          holidays
          python-multipart
        ];

        memecal = python.pkgs.buildPythonApplication {
          pname = "memecal";
          version = "0.1.0";
          pyproject = true;
          src = ./.;

          build-system = [ python.pkgs.setuptools ];
          dependencies = runtimeDeps python.pkgs;

          nativeCheckInputs = [ python.pkgs.pytestCheckHook ];
          # Die Netzwerk-Tests sind als solche markiert und laufen im Sandbox-
          # Build nicht mit.
          disabledTestPaths = [ "tests/test_youtube_live.py" ];

          meta = with pkgs.lib; {
            description = "Meme-Kalender";
            mainProgram = "memecal";
            platforms = platforms.linux;
          };
        };

        dockerImage = pkgs.dockerTools.buildLayeredImage {
          name = "meme-calendar";
          tag = "latest";
          contents = [
            memecal
            pkgs.cacert          # httpx braucht das CA-Bundle für YouTube
            pkgs.tzdata
          ];
          config = {
            Entrypoint = [ "${memecal}/bin/memecal" ];
            Cmd = [ "--host" "0.0.0.0" "--port" "8000" ];
            ExposedPorts."8000/tcp" = { };
            Env = [
              "MEMECAL_DATA_DIR=/data"
              "SSL_CERT_FILE=${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt"
              # Ohne TZDIR findet glibc die Zonendaten im Minimal-Image nicht
              # und der Container läuft auf UTC. Das verschiebt den Tageswechsel
              # und damit das Freischalten der Türchen um zwei Stunden.
              "TZDIR=${pkgs.tzdata}/share/zoneinfo"
              "TZ=Europe/Berlin"
            ];
            Volumes."/data" = { };
          };
        };
      in
      {
        packages = {
          default = memecal;
          inherit memecal dockerImage;
        };

        apps.default = flake-utils.lib.mkApp { drv = memecal; };

        devShells.default = pkgs.mkShell {
          packages = [
            (python.withPackages (ps: runtimeDeps ps ++ [
              ps.pytest
              ps.pytest-asyncio
            ]))
            pkgs.ruff
            pkgs.docker-compose
            pkgs.sqlite
            pkgs.curl
            pkgs.jq
          ];

          shellHook = ''
            export MEMECAL_DATA_DIR="$PWD/data"
            export PYTHONPATH="$PWD:$PYTHONPATH"
            echo "Meme-Kalender devShell"
            echo "  python -m memecal --reload   # lokal auf :8000"
            echo "  pytest                       # Tests"
          '';
        };
      });
}
