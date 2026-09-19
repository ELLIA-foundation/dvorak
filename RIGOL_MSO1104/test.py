"""Connect to the Rigol MSO1104Z over LAN and run a basic identity check."""

from rigol_scope import open_scope


def main():
    rm, scope, resource, idn = open_scope()
    try:
        print(f"Backend:  {rm}")
        print(f"Resource: {resource}")
        print(f"IDN:      {idn}")

        print("\nChannel 1 status:")
        print(f"  display: {scope.query(':CHANnel1:DISPlay?').strip()}")
        print(f"  scale:   {scope.query(':CHANnel1:SCALe?').strip()} V/div")
        print(f"  offset:  {scope.query(':CHANnel1:OFFSet?').strip()} V")

        print("\nTimebase:")
        print(f"  scale:   {scope.query(':TIMebase:MAIN:SCALe?').strip()} s/div")

        print("\nTrigger:")
        print(f"  status:  {scope.query(':TRIGger:STATus?').strip()}")
        print(f"  source:  {scope.query(':TRIGger:EDGe:SOURce?').strip()}")
    finally:
        scope.close()
        rm.close()


if __name__ == "__main__":
    main()
