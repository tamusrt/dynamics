"""Reading and writing .ork containers.

An .ork file is normally a zip archive holding one XML document (rocket.ork),
possibly alongside decal images. Older files may be gzip-compressed or plain
XML; all three are handled and each is re-saved in the format it came in.
"""

import copy
import gzip
import io
import zipfile
import xml.etree.ElementTree as ET


class OrkLoadError(Exception):
    pass


class OrkDocument:
    """A loaded .ork file: parsed XML tree plus what's needed to re-save it."""

    def __init__(self, path):
        self.path = str(path)
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except OSError as e:
            raise OrkLoadError(f"Can't read {path}: {e}")

        self.container = "xml"
        self.zip_entries = []  # (ZipInfo, bytes) for non-rocket entries (e.g. decals)
        self.xml_name = "rocket.ork"
        if zipfile.is_zipfile(io.BytesIO(raw)):
            self.container = "zip"
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                infos = z.infolist()
                if not infos:
                    raise OrkLoadError(f"{path} is an empty zip archive -- not a valid .ork file.")
                ork = [i for i in infos if i.filename.lower().endswith(".ork")]
                xml_info = ork[0] if ork else infos[0]
                self.xml_name = xml_info.filename
                xml_bytes = None
                for info in infos:
                    data = z.read(info)
                    if info.filename == self.xml_name:
                        xml_bytes = data
                    else:
                        self.zip_entries.append((info, data))
        elif raw[:2] == b"\x1f\x8b":
            self.container = "gzip"
            xml_bytes = gzip.decompress(raw)
        else:
            xml_bytes = raw

        try:
            parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
            parser.feed(xml_bytes)
            self.root = parser.close()
        except ET.ParseError as e:
            raise OrkLoadError(f"Couldn't parse the XML inside {path}: {e}")
        if self.root.tag != "openrocket" or self.root.find("rocket") is None:
            raise OrkLoadError(f"{path} doesn't look like an OpenRocket file "
                               f"(root <{self.root.tag}>, expected <openrocket> containing <rocket>).")

    def simulation_count(self):
        sims = self.root.find("simulations")
        return 0 if sims is None else len(sims.findall("simulation"))

    def strip_sim_data(self, root=None):
        """Drop stored flight data and mark simulations outdated.

        The simulation setups (motor, launch conditions) are kept so the
        variants can be re-simulated straight away; only the stale results
        are removed (they also make up most of a .ork's file size).
        """
        root = self.root if root is None else root
        for sim in root.iter("simulation"):
            for fd in sim.findall("flightdata"):
                sim.remove(fd)
            sim.set("status", "outdated")

    def clone_root(self):
        return copy.deepcopy(self.root)

    def serialize(self, root=None):
        return ET.tostring(self.root if root is None else root, encoding="utf-8", xml_declaration=True)

    def save(self, out_path, root=None, xml_bytes=None):
        if xml_bytes is None:
            xml_bytes = self.serialize(root)
        if self.container == "zip":
            with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr(self.xml_name, xml_bytes)
                for info, data in self.zip_entries:
                    z.writestr(info, data)
        elif self.container == "gzip":
            with open(out_path, "wb") as f:
                f.write(gzip.compress(xml_bytes))
        else:
            with open(out_path, "wb") as f:
                f.write(xml_bytes)
