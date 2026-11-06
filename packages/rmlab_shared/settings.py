from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MainSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RMLAB_", env_file=".env", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8080
    data_root: Path = Path("./data")
    jetson_url: str = "http://192.168.1.15:8090"
    jetson_ssh: str = "bill@192.168.1.15"
    jetson_hostname: str = "bill-desktop"
    chirpstack_url: str = "http://127.0.0.1:8081"
    mqtt_host: str = "127.0.0.1"
    mqtt_port: int = 1883
    main_advertise_host: str = "192.168.1.14"
    region: str = "US915"


class JetsonSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RMLAB_", env_file=".env", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8090
    main_url: str = "http://192.168.1.14:8080"
    mqtt_host: str = "192.168.1.14"
    mqtt_port: int = 1883
    sx1302_hal: Path = Path.home() / "sx1302_hal"
    packet_forwarder_conf: Path = Path("/etc/rmlab/global_conf.json")
    uart_device: str = "/dev/ttyUSB0"
    region: str = "US915"
    gateway_server: str = Field(
        default="192.168.1.14",
        description="Semtech UDP packet-forwarder server (ChirpStack gateway bridge)",
    )
    gateway_port_up: int = 1700
    gateway_port_down: int = 1700
