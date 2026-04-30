if __name__ == "__main__":
    from paperbrain.slack_bot import main

    main()
else:
    import sys

    from paperbrain import slack_bot as _module

    sys.modules[__name__] = _module

