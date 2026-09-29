# Data files of the interface

`world-land-110m.json` is the outline of the land masses drawn under the world
view of the atlas and of the offline site. It comes from
[Natural Earth](https://www.naturalearthdata.com/), the 1:110m physical vectors
(`ne_110m_land`, version 5), which are in the **public domain** (« No
permission is needed to use Natural Earth. Crediting the authors is
unnecessary. »). The polygons' rings were kept as they are, with longitudes and
latitudes rounded to a tenth of a degree and repeated points removed.

Format `cartolex-world/1`: `{"format", "source", "rings": [[lon0, lat0, lon1,
lat1, …], …]}`, each ring closed (its last point is its first).
