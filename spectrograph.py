from serial import Serial
from time import sleep
import socketio

from instrument import Instrument

PORTS = {
    "Mirror": 1,
    "LED": 2,
    "ThAr": 3,
    "Tung": 4
}
OFF_STATUS: dict = {"value": "Off", "color": "warning"}
ON_STATUS: dict = {"value": "On", "color": "success"}


class K8056(object):
    """
    K8056 - Class for controlling the velleman K8056 8 channel relay card

    K8056(device, repeat=0, wait=0)
    Give serial port as number or device file.
    For better reliability repeat instructions `repeat` times
    and `wait` seconds between execution.
    """

    def __init__(self, device, repeat=0, wait=0):
        self._serial = Serial(device, 2400)
        self.repeat = repeat
        self.wait = wait
        sleep(0.1)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self):
        """Close underlying serial device connection."""
        self._serial.close()

    def _process(self, instruction, byte, address):
        cksum = (243 - instruction - byte - address) & 255
        for i in range(self.repeat + 1):
            self._serial.write(bytearray([13, address, instruction, byte, cksum]))
            sleep(self.wait)

    def set(self, relay, address=1):
        """Set `relay` (9 for all) of card at `address` (default 1)."""
        if not 0 < relay < 10:
            raise Exception("invalid relay number")

        self._process(83, relay + 48, address & 255)

    def clear(self, relay, address=1):
        """Clear `relay` (9 for all) of card at `address` (default 1)."""
        if not 0 < relay < 10:
            raise Exception("invalid relay number")
        self._process(67, relay + 48, address & 255)

    def toggle(self, relay, address=1):
        """Toggle `relay` (9 for all) of card at `address` (default 1)."""
        if not 0 < relay < 10:
            raise Exception("invalid relay number")
        self._process(84, relay + 48, address & 255)

    def set_address(self, new=1, address=1):
        """Set address of card at `address` to `new`."""
        self._process(65, new & 255, address & 255)

    def send_byte(self, num, address=1):
        """Set relays to `num` (in binary mode)."""
        self._process(66, num & 255, address & 255)

    def emergency_stop(self):
        """Clear all relays on all cards. emergency purpose."""
        self._process(69, 1, 1)

    def force_address(self):
        """Reset all cards to address 1."""
        self._process(70, 1, 1)

    def get_address(self):
        """Display card address on LEDs."""
        self._process(68, 1, 1)


class Spectrograph(Instrument):
    """
    Wrapper class for whatever spectrograph is to be used.
    Interfaces with the backend in order to coordinate with other instruments.

    Attributes:
    -----------
        OBSERVING_MODES: dict
            A constant to translate the observing modes to be chosen,
            (object, dark, flat, thar) with the physical ports that need to
            be flipped
        status_dictionary: dict
            The initial status of the spectrograph to be sent to the backend
            and where the statuses are kept track of during the process of
            operation.
        device: None | K8056
            The physical device to be controlled by this file.
            NOTE: is None if there is no device, meaning a simulation is being
                  run
        simulator: boolean
            A boolean value for whether or not a physical device is connected.
            Set on instantiation
            TODO: make so that simulation running is a flag on file run.

    Mehtods:
    --------
        turn_on(port): None
            Turns on one of the given ports (mirror, led, thar, tung)
        turn_off(port): None
            Turns off one of the given port
        callbacks(): None
            The callbacks unique to the spectrograph which need to be listened
            to for correct operation of the spectrograph
        get_instrument_name(): str
            TODO: programmatic way to get the instrument name
            Gets the name of the connected physical spectrograph
    """

    # The following are the different observing modes to be used
    # for the statuses. Use these dictionaries to update the statuses
    OBSERVING_MODES = {
        "object": {
            "Mirror": OFF_STATUS,
            "LED": OFF_STATUS,
            "ThAr": OFF_STATUS,
            "Tung": OFF_STATUS,
        },
        "dark": {
            "Mirror": ON_STATUS,
            "LED": OFF_STATUS,
            "ThAr": OFF_STATUS,
            "Tung": OFF_STATUS,
        },
        "flat": {
            "Mirror": ON_STATUS,
            "LED": ON_STATUS,
            "ThAr": OFF_STATUS,
            "Tung": OFF_STATUS,
        },
        "thar": {
            "Mirror": ON_STATUS,
            "LED": OFF_STATUS,
            "ThAr": ON_STATUS,
            "Tung": OFF_STATUS,
        },
    }

    # Sets the initial status dictionary
    status_dictionary = OBSERVING_MODES["object"]

    def __init__(self, device="/dev/cu.usbserial-AK068Y10", simulator=False):
        """
        Connects to the physical spectrograph if connected or else
        defines the spectrograph as a simulation only
        """
        if simulator:
            self.device = None  # Replace with instance of K8056
        else:
            self.device = K8056(device)

        self.simulation = simulator
        super().__init__()

    def turn_on(self, port):
        """
        Turns on the port of the given type

        Parameters:
        -----------
            port: str
                The port to be turned on
                NOTE: can be ("mirror", "led", "thar", "tung")
        """

        if self.device is not None:
            self.device.set(port)


    def turn_off(self, port):
        """
        Turns on the port of the given type

        Parameters:
        -----------
            port: str
                The port to be turned on
                NOTE: can be ("mirror", "led", "thar", "tung")
        """

        if self.device is not None:
            self.device.clear(port)


    # TODO: find out how to find the model name of the spectrograph
    def get_instrument_name(self):
        """
        Gets the name of the connected spectrograph if conencted
        """
        return "spectrograph"

    def callbacks(self):
        @Instrument.sio.on("set_obs_type")
        def set_obs_type(mode):
            mode_ports = self.OBSERVING_MODES[mode]

            for port_name, settings in mode_ports.items():
                status_val = settings["value"]
                port_num = PORTS[port_name]

                if status_val == "On":
                    self.turn_on(port_num)
                else:
                    self.turn_off(port_num)

            Instrument.sio.emit("update_status", data=(self.id, mode_ports))


        @Instrument.sio.on("prepare_observation")
        def prepare_observation( mode, obs_instructions ):
            set_obs_type(mode)

            Instrument.sio.emit( "spectrograph_changed_ports", obs_instructions )


def main():
    load_dotenv()

    simulation_choice: str = utils.get_env_variable("SHELYAK_SIMULATE")

    must_simulate: bool = False
    if simulation_choice.lower() == "true":
        must_simulate = True

    spectrograph = Spectrograph(simulator=must_simulate)


if __name__ == "__main__":
    main()