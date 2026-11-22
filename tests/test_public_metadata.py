from public_metadata import description

def test_private_labels_removed_but_attribution_kept():
    value='A funny GTA moment.\n\nShotsnip intro and edit.\nShotsnip capture abc-123\nShotsnip breakdown and commentary.\nOriginal moment: Creator\nhttps://example.com/stream'
    result=description(value)
    assert 'Shotsnip' not in result
    assert 'Original moment: Creator' in result
    assert 'https://example.com/stream' in result
