from utils.formatting import format_number


def test_format_number_thousands():
    assert format_number(1234567) == '1,234,567'


def test_format_number_small():
    assert format_number(42) == '42'


def test_format_number_zero():
    assert format_number(0) == '0'


def test_format_number_none():
    assert format_number(None) == '0'
