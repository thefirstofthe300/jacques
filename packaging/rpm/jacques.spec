Name:           jacques
Version:        0.1.0
Release:        1%{?dist}
Summary:        Automatic disc ripping daemon with web UI

License:        MIT
URL:            https://github.com/thefirstofthe300/jacques
Source0:        %{name}-%{version}.tar.gz
Source1:        jacques.service
Source2:        sysusers.jacques.conf
Source3:        tmpfiles.jacques.conf
Source4:        jacques.conf

BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  python3-setuptools
BuildRequires:  %{py3_dist setuptools}

# Runtime Python dependencies
Requires:       python3-fastapi
Requires:       python3-uvicorn
Requires:       python3-jinja2
Requires:       python3-multipart
Requires:       python3-sqlalchemy
Requires:       python3-aiosqlite
Requires:       python3-pydantic-settings
Requires:       python3-pyudev
Requires:       python3-httpx
Requires:       python3-rich

# External binaries — makemkv from RPMFusion, handbrake from RPMFusion
Requires:       makemkv
Requires:       HandBrake-cli

Requires(pre):  systemd-units
Requires(post): systemd-units
Requires(preun): systemd-units

%description
Jacques is an automatic disc ripping daemon. Insert a Blu-ray or DVD, and
Jacques rips it with MakeMKV, transcodes to H.265/HEVC with HandBrakeCLI,
looks up the title on The Movie Database (TMDb), and moves the result to a
Plex- or Jellyfin-compatible library layout. A lightweight web dashboard
shows job status in real time.

%prep
%autosetup -n %{name}-%{version}

%generate_buildrequires
%pyproject_buildrequires

%build
%pyproject_wheel

%install
%pyproject_install

# systemd unit
install -Dpm 0644 %{SOURCE1} %{buildroot}%{_unitdir}/jacques.service

# sysusers / tmpfiles
install -Dpm 0644 %{SOURCE2} %{buildroot}%{_sysusersdir}/jacques.conf
install -Dpm 0644 %{SOURCE3} %{buildroot}%{_tmpfilesdir}/jacques.conf

# Default config
install -Dpm 0640 %{SOURCE4} %{buildroot}%{_sysconfdir}/jacques/jacques.conf

%pre
%sysusers_create_compat %{SOURCE2}

%post
%systemd_post jacques.service
systemd-tmpfiles --create %{_tmpfilesdir}/jacques.conf || :

%preun
%systemd_preun jacques.service

%postun
%systemd_postun_with_restart jacques.service

%files
%license LICENSE
%{python3_sitelib}/jacques/
%{python3_sitelib}/jacques-*.dist-info/
%{_bindir}/jacques
%{_unitdir}/jacques.service
%{_sysusersdir}/jacques.conf
%{_tmpfilesdir}/jacques.conf
%dir %{_sysconfdir}/jacques
%config(noreplace) %{_sysconfdir}/jacques/jacques.conf

%changelog
* Sat Jul 05 2026 Jacques Maintainer <maintainer@example.com> - 0.1.0-1
- Initial package
