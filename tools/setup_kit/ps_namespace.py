"""Assign the donor's requests to private ProStreet stock model resources."""
from . import car_convert as cc, ps_attrib, v25_batch

TEMPLATE = 'FORDGT'
# Wheel images ride on roles the game asks for under the donor's name (00751860:
# %s_RIM_BLUR, %s_TREAD, %s_TIRE_N). A pack image no request reaches is never
# loaded and its draws do not show. The rim atlas takes RIM_BLUR as for Carbon
# additions: under TIRE the implicit TIRE_N would put the rubber normal on rims.
WHEEL_REQUESTS = (('{car}_WHEEL', 'RIM_BLUR'), ('TIRE_STYLE01', 'TREAD'), ('TIRE_STYLE02', 'TREAD'),
                  ('METAL_SWATCH', 'RIM'),
                  ('{car}_TIRE_N', 'TIRE_N'), ('TIRE_STYLE02_N', 'TIRE_N'),
                  # SPHERIZE's normal_resource_aliases identity needs a request too.
                  ('{car}_NEON', 'NEON'))


def donor_image_aliases(source, reference, car, donor, texture_map):
    """Answer donor image requests for this car privately, including absent lamps.

    GetUsedCarTextureInfo (00751860) names lamp images after the donor's CarParts,
    so an added car would load and release the donor pack's own entries; a release
    it never loaded leaves the donor's count wrapped and the donor later draws
    without them. Each donor image maps to this pack's image of the same role, or
    to an absent private name. Composited paint names are never mapped.
    """
    private = dict(texture_map)
    donor_images = {h for h, _ in cc.texture_blobs(reference)}
    donor_images -= {cc.name_hash(f'{donor}_{s}') for s in ('SKIN1', 'SKIN1B', 'SKIN2', 'SKIN3', 'SKIN4')}
    aliases = {}
    # v8 labels stop at 31 bytes, but requests use the full role hash. The
    # loader asks for these even when the donor pack has no matching image.
    lamps = {cc.name_hash(f'{car}_KIT{k:02d}_{kind}_{state}'):
             cc.name_hash(f'{donor}_KIT{k:02d}_{kind}_{state}')
             for k in range(100)
             for kind in ('HEADLIGHT', 'HEADLIGHT_GLASS', 'BRAKELIGHT', 'BRAKELIGHT_GLASS')
             for state in ('OFF', 'ON')}
    for hs, blob in cc.texture_blobs(source):
        r = blob[-148:-24]
        # Source labels are null padded and may be truncated; trust exact ones only.
        name = bytes(r[89:89 + r[88]]).split(b'\0')[0].decode('ascii')
        exact = cc.name_hash(name) == hs and name.startswith(car + '_')
        wanted = cc.name_hash(donor + name[len(car):]) if exact else None
        if wanted in donor_images and hs in private:
            aliases[wanted] = private[hs]
        if hs in lamps and hs in private:
            aliases[lamps[hs]] = private[hs]
    for name, role in WHEEL_REQUESTS:
        image = private.get(cc.name_hash(name.format(car=car)))
        if image is not None:
            wanted = cc.name_hash(f'{donor}_{role}')
            cc.require(aliases.setdefault(wanted, image) == image, 'two wheel images answer one request')
    # At most two factory styles per car. Both need independent resident images;
    # the normal image keeps NEON while free trim requests load the rotor atlases.
    rotors = [private[h] for h in (cc.name_hash('ROTOR_' + s) for s in ('00', '01', '02', '05')) if h in private]
    free = [cc.name_hash(f'{donor}_{role}') for role in ('DOOR_HANDLE', 'SIDELIGHT')
            if cc.name_hash(f'{donor}_{role}') not in aliases]
    cc.require(len(rotors) <= len(free), 'factory rotor images exceed free donor requests')
    aliases.update(zip(free, rotors))
    for h in donor_images - aliases.keys():
        aliases[h] = cc.name_hash(f'NFSAD_{car}_ABSENT_{h:08X}')
    return sorted(aliases.items())


def stock_packs(car, donor, geometry, textures, globals_raw, rotor_data,
                donor_geometry, donor_textures, template_geometry, template_textures, mw_global, *, decode=None):
    reference = v25_batch.output_models(donor_geometry)
    # A stock wing rides on the body if the donor has no separate SPOILER request.
    body_roles = () if f'{donor}_KIT00_SPOILER_A' in reference else ('SPOILER',)
    geo, tex, images, reflector = v25_batch.convert_one(
        geometry, textures, template_geometry, template_textures, globals_raw, rotor_data, mw_global,
        car, body_roles, decode=decode)
    models, solidmap = cc.isolate(
        v25_batch.output_models(geo), reference, donor, car, images, missing_kit_fallback=False,
        source_prefix=car, image_aliases=donor_image_aliases(textures, donor_textures, car, donor, images),
        rebind={cc.name_hash(f'{TEMPLATE}_SKIN1'): cc.name_hash(f'{donor}_SKIN1')})
    # Keep the stock model mesh fields and the reflector's material trailer.
    output, _ = cc.write_geometry(geo, models)
    if reflector:
        output = cc.append_material_rows(output, {v25_batch.REAR_REFLECTOR:
            v25_batch.reflector_row(globals_raw, mw_global)})
    cc.validate_geometry(output, material_names=(v25_batch.REAR_REFLECTOR_NAME,) if reflector else ())
    return {'GEOMETRY.BIN': output, 'TEXTURES.BIN': tex, 'SOLIDMAP.BIN': solidmap}


def convert(car, donor, source, mw):
    """Convert the selected PC snapshot with the player's Most Wanted template."""
    shared = cc.unpack_file(source['GLOBAL/GLOBALB.LZC'])
    bins = stock_packs(
        car, donor, source[f'CARS/{car}/GEOMETRY.BIN'], source[f'CARS/{car}/TEXTURES.BIN'],
        shared, cc.unpack_file(source['GLOBAL/INGAMEA.BUN']),
        mw[f'CARS/{donor}/GEOMETRY.BIN'], mw[f'CARS/{donor}/TEXTURES.BIN'],
        mw[f'CARS/{TEMPLATE}/GEOMETRY.BIN'], mw[f'CARS/{TEMPLATE}/TEXTURES.BIN'],
        cc.unpack_file(mw['GLOBAL/GLOBALB.LZC']))
    bins.update(ps_attrib.sidecars(source['GLOBAL/ATTRIBUTES.BIN'], mw['GLOBAL/ATTRIBUTES.BIN'], car, donor))
    collision = cc.collision_sidecar(shared, car, donor)
    if collision is not None:
        bins['COLLISION.BIN'] = collision
    return bins
