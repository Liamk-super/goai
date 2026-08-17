/** 四位专业 Agent 的小型 SVG 符号（path，24×24 视口，中心 0,0）。
 *  产品与交付：六角螺帽；用户证据：人形；商业与投资：天平；
 *  时间地域政策：经纬球。均为几何单色剪影。 */
export const AGENT_GLYPHS: Record<string, string> = {
  "product-engineering":
    "M0,-9 L7.8,-4.5 L7.8,4.5 L0,9 L-7.8,4.5 L-7.8,-4.5 Z M0,-4 A4,4 0 1,0 0,4 A4,4 0 1,0 0,-4 Z",
  "user-evidence":
    "M0,-8 A3.6,3.6 0 1,1 0,-0.8 A3.6,3.6 0 1,1 0,-8 Z M-6.5,8 C-6.5,3 -3.5,1 0,1 C3.5,1 6.5,3 6.5,8 Z",
  "business-investment":
    "M-0.9,-9 L0.9,-9 L0.9,7 L5,7 L5,9 L-5,9 L-5,7 L-0.9,7 Z M-7.5,-5 L-2.5,-5 L-5,-0.5 Z M2.5,-5 L7.5,-5 L5,-0.5 Z M-1,-6.8 L1,-6.8 L1,-5 L-1,-5 Z",
  "geo-policy-trend":
    "M0,-9 A9,9 0 1,1 0,9 A9,9 0 1,1 0,-9 Z M0,-9 C4,-5 4,5 0,9 C-4,5 -4,-5 0,-9 Z M-8.6,-2.5 L8.6,-2.5 M-8.6,2.5 L8.6,2.5",
  default:
    "M0,-8 A8,8 0 1,1 0,8 A8,8 0 1,1 0,-8 Z",
};

export function glyphViewTransform(scale = 1): string {
  return `scale(${scale})`;
}
