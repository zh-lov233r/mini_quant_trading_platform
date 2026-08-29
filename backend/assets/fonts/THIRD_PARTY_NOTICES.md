# Report font notices

The PDF report runtime depends on `scifont==0.1.9`, which distributes the
Noto Sans SC variable TrueType font used and embedded by ReportLab.

- Noto Sans SC is developed by Google and Adobe.
- The font is distributed under the SIL Open Font License 1.1.
- The installed package retains its distribution license under
  `scifont-0.1.9.dist-info/licenses/LICENSE` and describes bundled font
  licenses in its package metadata.
- Upstream font license: <https://github.com/notofonts/noto-cjk/blob/main/Sans/LICENSE>
- Runtime package: <https://pypi.org/project/scifont/>

`REPORT_FONT_PATH` may override the packaged font only when the supplied font
has compatible CJK coverage and redistribution/embedding rights.
