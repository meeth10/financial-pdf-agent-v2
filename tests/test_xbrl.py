from mcp_servers.retrieval.xbrl import parse_xbrl


def test_parse_ixbrl_concepts_scale_sign_and_period(tmp_path):
    path = tmp_path / "sample_ixbrl.xhtml"
    path.write_text(
        '''<?xml version="1.0" encoding="UTF-8"?>
<html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:ind-as="http://example.test/ind-as" xmlns:iso4217="http://www.xbrl.org/2003/iso4217">
  <body>
    <ix:header>
      <xbrli:unit id="INR"><xbrli:measure>iso4217:INR</xbrli:measure></xbrli:unit>
      <xbrli:context id="c1">
        <xbrli:entity><xbrli:identifier scheme="test">ABC</xbrli:identifier></xbrli:entity>
        <xbrli:period><xbrli:startDate>2025-04-01</xbrli:startDate><xbrli:endDate>2026-03-31</xbrli:endDate></xbrli:period>
      </xbrli:context>
    </ix:header>
    <table>
      <tr><td>Revenue from operations</td><td><ix:nonFraction name="ind-as:RevenueFromOperations" contextRef="c1" unitRef="INR" scale="5">123.45</ix:nonFraction></td></tr>
      <tr><td>Profit before income tax</td><td><ix:nonFraction name="ind-as:ProfitBeforeIncomeTax" contextRef="c1" unitRef="INR" scale="5" sign="-">7.50</ix:nonFraction></td></tr>
    </table>
  </body>
</html>''',
        encoding="utf-8",
    )
    facts = parse_xbrl(path)
    by_metric = {fact["metric"]: fact for fact in facts}
    assert by_metric["revenue"]["period"] == "FY2026"
    assert by_metric["revenue"]["value"] == 12345000.0
    assert by_metric["pbt"]["value"] == -750000.0
    assert by_metric["revenue"]["unit"] == "INR"
