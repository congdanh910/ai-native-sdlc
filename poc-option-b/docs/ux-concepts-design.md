# UI/UX concept cho từng dự án

_2026-10-22. Đề xuất, chờ người dùng duyệt. Bối cảnh: dự án thực tế có thể có hoặc không có designer/Figma; đa số
khách có logo, màu và font có thể có hoặc không; không có thì nền tảng đưa 3–5 concept mặc định để chọn. Người dùng sẽ
reset attendance và chạy lại từ đầu sau khi test xong các phần khác, nên tính năng phải sẵn sàng từ Discovery/SDS._

## Vấn đề

SDS §3 chỉ mô tả UI bằng chữ. Không có concept để chọn, không có design system dùng chung, không ai duyệt "nhìn có
đúng thiết kế không". Mỗi story AI vẽ một kiểu; khách có brand guideline thì không có chỗ đưa vào.

## Đầu vào (theo những gì khách có)

| Khách có | Nền tảng làm |
|---|---|
| Figma / designer | Link Figma và ảnh xuất ra (hoặc file token) là nguồn chính; concept bám theo đó, AI không tự sáng tác |
| Logo, có thể kèm màu, font | Áp brand lên 3–5 concept khác nhau về bố cục, mật độ, kiểu component; màu lấy từ logo khi khách không đưa |
| Không có gì | 3–5 concept mặc định của nền tảng |

Khai báo khi tạo run SDS (khung **Brand and UX** trong New request), lưu thành nguồn của run:
logo (file), màu chính/phụ (tuỳ chọn), font (tuỳ chọn), link Figma (tuỳ chọn), thiết bị đích (desktop, tablet,
mobile, kiosk), mức accessibility (mặc định WCAG 2.2 AA), giọng điệu (trang trọng / thân thiện), ngôn ngữ giao diện.

## Concept mặc định của nền tảng (`ui-presets/`, cấu hình được, không cứng trong code)

Mỗi preset là một bộ token (màu, chữ, spacing, radius, bóng), quy tắc component và một khung HTML mẫu:

1. **Enterprise clean**: nền trung tính, bảng dày thông tin, sidebar cố định. Hợp hệ thống nội bộ.
2. **Friendly modern**: bo tròn, nhiều khoảng trắng, thẻ (card). Hợp cổng nhân viên, khách hàng.
3. **Data dashboard**: lưới thẻ số liệu, biểu đồ, bộ lọc trên cùng. Hợp màn hình quản lý.
4. **Mobile first**: điều hướng dưới, nút lớn, một cột. Hợp app hiện trường, chấm công.
5. **High contrast accessible**: tương phản cao, chữ lớn, focus rõ. Hợp khách công, yêu cầu accessibility cao.

Số concept mỗi lần: `ui.concepts` trong project.yaml (mặc định 3, tối đa 5).

## Luồng

1. **Discovery / SRS**: BA ghi yêu cầu UX (vai, thiết bị, ngôn ngữ, accessibility, brand) vào baseline và SRS.
2. **SDS – bước UI Concepts** (sau bước Options kiến trúc, cùng run SDS): vai **UX Designer** (AI) đưa N concept, mỗi
   concept gồm token, prototype HTML 3–4 màn hình chính lấy từ SRS (dữ liệu mẫu, các trạng thái rỗng/lỗi/đang tải),
   ảnh chụp ở các thiết bị đích, kiểm tương phản màu, và lý do hợp với dự án. Controller kiểm: token hợp lệ, tương phản
   đạt mức accessibility, prototype mở được.
3. **Cổng UI Concept** (vai người UX, mặc định PO): xem prototype ngay trên control room (iframe sandbox), chọn một
   concept kèm lý do, hoặc yêu cầu concept khác hay chỉnh (đổi màu nhấn, dày hơn…). SDS §3 ghi concept đã chọn.
4. **PR của SDS** thêm `docs/ui/`: `UI.md` (concept, nguyên tắc, khi nào dùng component nào), `design-tokens.json`
   (nguồn duy nhất), `concepts/<đã chọn>/` (prototype để tra lại), `brand/` (logo khách đưa).
5. **Foundation**: onboarding thêm vào AGENTS.md và REVIEW.md luật UI (chỉ dùng token và component, không hard-code
   màu/style); plan dựng design system: token thành CSS variables/theme, component nền (nút, form, bảng, layout, thông
   báo, trạng thái), trang mẫu các component.
6. **Mỗi story**: spec ghi trạng thái UI; Developer dùng component có sẵn; Code Reviewer bắt màu/style viết tay;
   Acceptance chụp ảnh từng bước như hiện nay; cổng QC có thêm mục **UI đúng concept** (tick của người UX khi
   `ui.review: gated`).

## Cấu hình

```yaml
ui:
  guide: docs/ui/UI.md
  tokens: docs/ui/design-tokens.json
  concepts: 3                # 1..5
  devices: [desktop, mobile]
  accessibility: wcag-aa
  review: gated              # gated | auto | skip: người UX tick "UI đúng concept" ở cổng Acceptance
```

Nền tảng: thư mục `ui-presets/` (5 preset trên), setting số concept tối đa, vai `ux_designer` trong engine của vai
suy nghĩ.

## Giai đoạn

- **U1**: khung Brand and UX khi tạo run SDS; preset mặc định; bước UI Concepts + cổng chọn; `docs/ui/` vào PR của SDS.
- **U2**: Foundation dựng design system từ token; luật UI trong AGENTS.md/REVIEW.md; mục "UI đúng concept" ở cổng QC.
- **U3** (về sau): nhập Figma (token, ảnh khung), so ảnh tự động (visual regression) trong Acceptance.

U1 + U2 là đủ cho lần chạy lại attendance từ đầu.
