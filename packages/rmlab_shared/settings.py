from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MainSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RMLAB_", env_file=".env", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8080
    data_root: Path = Path("./data")
    # Defaults are seeds only; JetsonLocator follows DHCP via LAN scan + mDNS.
    jetson_url: str = "http://bill-desktop.local:8090"
    jetson_ssh: str = "bill@bill-desktop.local"
    jetson_hostname: str = "bill-desktop"
    jetson_ssh_alias: str = "jetson"
    chirpstack_url: str = "http://127.0.0.1:8081"
    mqtt_host: str = "127.0.0.1"
    mqtt_port: int = 1883
    # "auto" = advertise the hub's current LAN IPv4 (see advertise_host()).
    main_advertise_host: str = "auto"
    region: str = "US915"


class JetsonSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RMLAB_", env_file=".env", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8090
    # "auto" = hub_discover follows DHCP (mDNS + LAN scan).
    main_url: str = "auto"
    mqtt_host: str = "auto"
    mqtt_port: int = 1883
    sx1302_hal: Path = Path.home() / "sx1302_hal"
    packet_forwarder_conf: Path = Path("/etc/rmlab/global_conf.json")
    uart_device: str = "/dev/ttyUSB0"
    region: str = "US915"
    hub_hostname: str = "bill-GB-BXBT-1900"
    gateway_server: str = Field(
        default="auto",
        description="Semtech UDP packet-forwarder server (ChirpStack gateway bridge)",
    )
    gateway_port_up: int = 1700
    gateway_port_down: int = 1700
