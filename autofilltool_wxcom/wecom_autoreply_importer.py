import os
import sys
import time
import random
import threading
import pandas as pd
from playwright.sync_api import sync_playwright

if sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except:
        pass

def human_delay(min_sec=0.3, max_sec=0.7):
    time.sleep(random.uniform(min_sec, max_sec))

def wait_for_ready_or_enter(page):
    enter_pressed = False

    def listen_enter():
        nonlocal enter_pressed
        try:
            input()
            enter_pressed = True
        except:
            pass

    t = threading.Thread(target=listen_enter, daemon=True)
    t.start()

    print("="*60)
    print("👉 请在打开的浏览器中【使用企业微信扫码登录】")
    print("👉 进入【自动回复】列表页后，在命令行按【Enter 回车键】开始！")
    print("="*60 + "\n")

    start_time = time.time()
    while time.time() - start_time < 300:
        if enter_pressed:
            print("🚀 已确认，立即开始自动化批量填表！")
            return True
        time.sleep(0.8)
    return True

def click_add_rule_btn(page):
    """从列表页点击【添加规则】进入编辑页"""
    add_btns = [
        ".js_add_rule",
        "a.qui_btn_primary:has-text('规则')",
        "a:has-text('添加规则')",
        "text=+ 添加规则",
        "text=添加规则",
        "text=新建规则",
        "text=+ 新建规则",
        "button:has-text('添加规则')",
    ]
    for sel in add_btns:
        try:
            btn = page.locator(sel).first
            if btn.is_visible():
                btn.click()
                human_delay(0.8, 1.2)
                # 判断是否进入编辑页
                if page.locator("input.csAutoReply_rule_name_input").count() > 0:
                    return True
        except:
            pass
    return False

def set_match_mode_for_row(page, row_idx, match_mode):
    """
    对第 row_idx 行的关键字下拉框设置匹配模式。
    DOM: .csAutoReply_inputWithDropMenu → a.csAutoReply_inputWithDropMenu_menu → 展开后 li 选项
    """
    try:
        # 找到所有匹配模式下拉容器（每一行关键字对应一个）
        dropdowns = page.locator(".csAutoReply_inputWithDropMenu_menu, a.ww_btn_Dropdown.csAutoReply_inputWithDropMenu_menu")
        btn = dropdowns.nth(row_idx)
        if not btn.is_visible():
            return

        # 当前标签文字
        current_label = btn.locator(".ww_btn_Dropdown_label").text_content().strip()

        if "完全" in match_mode or "精准" in match_mode or "等于" in match_mode:
            target = "完全匹配"
        else:
            target = "包含"

        if current_label == target:
            return  # 已经是目标模式，不用切换

        # 点击下拉按钮展开菜单
        btn.click()
        human_delay(0.2, 0.4)

        # 点击对应选项
        option_sel = f"text={target}"
        page.locator(option_sel).last.click()
        human_delay(0.2, 0.4)

    except Exception as e:
        print(f"    ⚠️ 设置第 {row_idx+1} 行匹配模式时出错: {e}")

def add_single_rule(page, rule_name, keywords_raw, match_mode, reply_content, index, total):
    print(f"\n👉 [{index + 1}/{total}] 录入规则: 【{rule_name}】")

    # ─── 1. 填写规则名 ───
    print(f"  📝 规则名: {rule_name}")
    try:
        name_input = page.locator("input.csAutoReply_rule_name_input").first
        name_input.click()
        human_delay(0.1, 0.2)
        name_input.fill("")
        name_input.fill(rule_name)
    except Exception as e:
        print(f"  ⚠️ 填写规则名异常: {e}")

    human_delay(0.3, 0.5)

    # ─── 2. 填写关键字（含设置匹配模式）───
    keywords = [k.strip() for k in str(keywords_raw)
                .replace("，", ",").replace("；", ";").replace(";", ",")
                .split(",") if k.strip()]
    print(f"  📌 共 {len(keywords)} 个关键字: {keywords}")

    for idx, kw in enumerate(keywords):
        try:
            # idx > 0 时，先点击【添加】追加新的输入行
            if idx > 0:
                add_link = page.locator(
                    ".app_stage_section a:has-text('添加'), "
                    "a.ww_inputWidthDropMenuGroup_item_close ~ a, "
                    "text=添加"
                ).last
                add_link.click()
                human_delay(0.3, 0.5)

            # 定位第 idx 个关键字输入框
            kw_inputs = page.locator("input.csAutoReply_rule_keyword_input")
            kw_input = kw_inputs.nth(idx)
            kw_input.click()
            human_delay(0.1, 0.2)
            kw_input.fill("")
            kw_input.fill(kw)
            human_delay(0.2, 0.3)

            # 设置匹配模式下拉框（每行独立设置）
            set_match_mode_for_row(page, idx, match_mode)

            print(f"    ✅ [{idx + 1}/{len(keywords)}] {kw} ({match_mode})")

        except Exception as e:
            print(f"    ⚠️ 关键字 [{kw}] 录入异常: {e}")

    human_delay(0.4, 0.6)

    # ─── 3. 填写回复内容 ───
    print("  💬 填写回复内容...")
    try:
        reply_box = page.locator(
            "div[contenteditable='true'], "
            "textarea[placeholder*='输入消息内容'], "
            ".ww_richEditor_textarea, "
            "textarea"
        ).first
        reply_box.click()
        human_delay(0.2, 0.3)
        try:
            reply_box.fill(reply_content)
        except:
            page.keyboard.type(reply_content)
    except Exception as e:
        print(f"  ⚠️ 填写回复内容异常: {e}")

    human_delay(0.5, 0.9)

    # ─── 4. 点击【保存】 ───
    print("  💾 保存规则...")
    try:
        save_btn = page.locator(
            "a.qui_btn:has-text('保存'), "
            "button:has-text('保存'), "
            "text=保存"
        ).last
        save_btn.click()
        # 等待保存完成并返回列表页（检测列表页标志）
        try:
            page.wait_for_selector(
                ".js_add_rule, a:has-text('添加规则'), text=添加规则, text=新建规则",
                timeout=5000
            )
        except:
            human_delay(1.5, 2.5)
    except Exception as e:
        print(f"  ⚠️ 保存异常: {e}")
        human_delay(1.5, 2.5)

    print(f"✅ 第 {index + 1} 条 【{rule_name}】 保存完成！")

    # ─── 5. 保存后自动点击【添加规则】进入下一条 ───
    if index + 1 < total:
        print(f"  ➡️  自动点击【添加规则】准备录入第 {index + 2} 条...")
        human_delay(0.5, 0.9)
        ok = click_add_rule_btn(page)
        if not ok:
            print("  ⚠️ 未能自动进入下一条编辑页，等待 2 秒后重试...")
            human_delay(1.5, 2.5)
            click_add_rule_btn(page)

    return True

def run_import():
    excel_path = "autoreply_rules_fmtbio.xlsx"
    if not os.path.exists(excel_path):
        excel_path = "autoreply_rules.xlsx"
    if not os.path.exists(excel_path):
        print(f"❌ 未找到规则表格文件：{excel_path}")
        return

    df = pd.read_excel(excel_path)
    print(f"📊 已从 [{excel_path}] 载入 {len(df)} 条规则！")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=["--start-maximized"])
        context = browser.new_context(no_viewport=True)
        page = context.new_page()

        target_url = "https://work.weixin.qq.com/wework_admin/frame#customer/autoReply"
        print(f"🌐 打开企微后台：{target_url}")
        page.goto(target_url)

        wait_for_ready_or_enter(page)
        human_delay(0.8, 1.2)

        # 先点一次添加规则，进入第 1 条的编辑页
        print("🖱️  自动点击【添加规则】进入第 1 条编辑页...")
        if not click_add_rule_btn(page):
            print("⚠️ 未能自动进入编辑页，请手动点击【添加规则】，脚本等待中...")
            for _ in range(20):
                if page.locator("input.csAutoReply_rule_name_input").count() > 0:
                    break
                time.sleep(1)

        human_delay(0.5, 1.0)

        success_count = 0
        for index, row in df.iterrows():
            rule_name    = str(row.get("规则名称", f"规则_{index+1}")).strip()
            keywords_raw = str(row.get("关键词", "")).strip()
            match_mode   = str(row.get("匹配模式", "包含")).strip()
            reply_content= str(row.get("回复内容", "")).strip()

            try:
                ok = add_single_rule(page, rule_name, keywords_raw, match_mode, reply_content, index, len(df))
                if ok:
                    success_count += 1
            except Exception as e:
                print(f"⚠️ 第 {index + 1} 条处理异常: {e}")
                human_delay(1.0, 2.0)

        print("\n" + "="*60)
        print(f"🎉 批量录入完成！成功率: {success_count}/{len(df)}。")
        print("💡 浏览器保持开启，确认无误后在终端按 Enter 键退出。")
        print("="*60)
        input()
        browser.close()

if __name__ == "__main__":
    run_import()
