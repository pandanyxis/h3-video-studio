export function mediaVisualDescriptor(asset) {
  if (asset?.type === "video") {
    const w = Number(asset.contact_sheet_width) || 1;
    const h = Number(asset.contact_sheet_height) || 1;
    if (!asset.contact_sheet_url && !asset.sheet) return null;
    return { src: asset.contact_sheet_url, bitmap: asset.sheet, w, h, ar: w / h, kind: "Video sheet" };
  }
  if (asset?.type === "image") {
    const w = Number(asset.width || asset.prepared_width) || 1;
    const h = Number(asset.height || asset.prepared_height) || 1;
    return { src: asset.prepared_url || asset.content_url || asset.preview_url, bitmap: asset.bitmap, w, h, ar: w / h, kind: "Picture" };
  }
  return null;
}

export function referenceCaption(asset) {
  return /^<(Picture|Video) [1-9]\d*>$/.test(asset?.reference || "") ? asset.reference : "";
}
