import argparse
import requests
from bs4 import BeautifulSoup
import re
import json
import logging
from urllib.parse import quote
import time

# --- 配置日志记录 ---
# 配置日志记录器，用于将网络请求的详细信息保存到 temp.log 文件
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("temp.log", mode='w', encoding='utf-8'), # 写入文件
        # logging.StreamHandler() # 同时在控制台输出
    ]
)

# --- URL常量 ---
# 定义所有需要用到的URL，方便管理和修改
BASE_URL = "https://client.vpn.nuist.edu.cn/http/webvpnf798bff316e8cb600a95f8a16f827ac1"
PERSONAL_INFO_URL = f"{BASE_URL}/chosen/redirect/chosen/personal"
SELECT_BEDS_URL = f"{BASE_URL}/chosen/redirect/chosen/selectBeds"
FLOOR_SHOW_API_URL = f"{BASE_URL}/chosen/api/v2/chosen/student/floorShow"
SAVE_CHOSEN_API_URL = f"{BASE_URL}/chosen/api/v2/chosen/student/saveChosen"

def log_request(response):
    """记录请求和响应的详细信息到日志文件"""
    logging.info("----------- New Request -----------")
    logging.info(f"Request URL: {response.request.url}")
    logging.info(f"Request Method: {response.request.method}")
    logging.info(f"Request Headers: {json.dumps(dict(response.request.headers), indent=2)}")
    if response.request.body:
        # 尝试解码POST请求体
        try:
            body = response.request.body.decode('utf-8')
            logging.info(f"Request Body: {body}")
        except Exception:
            logging.info(f"Request Body (bytes): {response.request.body}")
            
    logging.info(f"Response Status Code: {response.status_code}")
    logging.info(f"Response Headers: {json.dumps(dict(response.headers), indent=2)}")
    
    # 尝试以JSON或文本格式记录响应内容
    try:
        # 尝试解析为JSON
        response_json = response.json()
        logging.info(f"Response Body (JSON):\n{json.dumps(response_json, indent=2, ensure_ascii=False)}")
    except json.JSONDecodeError:
        # 如果不是JSON，则作为文本记录
        logging.info(f"Response Body (Text):\n{response.text[:1000]}...") # 记录前1000个字符以防内容过长
    logging.info("-----------------------------------\n")


def get_personal_info(session):
    """
    Step 1 & 2: 获取学生姓名和学院信息
    """
    try:
        print("Step 1: 正在获取个人信息...")
        response = session.get(PERSONAL_INFO_URL)
        response.raise_for_status() # 如果请求失败则抛出异常
        log_request(response)

        soup = BeautifulSoup(response.text, 'html.parser')
        personal_div = soup.find('div', class_='personal_left')

        if not personal_div:
            print("错误：无法在页面上找到个人信息。请检查Cookie是否正确或页面结构是否已更改。")
            return None, None

        p_tags = personal_div.find_all('p')
        name = p_tags[0].text.strip()
        college = p_tags[1].text.replace('学院：', '').strip()

        print(f"✅ 成功获取信息：姓名 - {name}, 学院 - {college}")
        return name, college

    except requests.exceptions.RequestException as e:
        print(f"错误：获取个人信息时网络请求失败: {e}")
        return None, None
    except (IndexError, AttributeError) as e:
        print(f"错误：解析个人信息页面失败，可能是页面结构已更改: {e}")
        return None, None


def get_selection_params(session, student_name, dept_name):
    """
    Step 3 (Part 1): 获取选房页面中的 token, subId, 和 buildingId
    """
    try:
        print("\nStep 2: 正在获取选房参数 (token, subId, buildingId)...")
        # 对中文参数进行URL编码
        params = {
            'studentName': student_name,
            'deptName': dept_name
        }
        response = session.get(SELECT_BEDS_URL, params=params)
        response.raise_for_status()
        log_request(response)

        soup = BeautifulSoup(response.text, 'html.parser')

        # 1. 提取 token
        token_input = soup.find('input', {'id': 'token', 'name': 'token'})
        if not token_input or 'value' not in token_input.attrs:
            print("错误：无法在页面上找到token。")
            return None, None, None
        token = token_input['value']

        # 2. 提取 subId 和 buildingId
        script_text = response.text
        sub_id_match = re.search(r"subId\s*=\s*'([^']+)';", script_text)
        building_id_match = re.search(r"buildingId\s*=\s*'([^']+)';", script_text)

        if not sub_id_match or not building_id_match:
            print("错误：无法在页面脚本中找到 subId 或 buildingId。")
            return None, None, None
        
        sub_id = sub_id_match.group(1)
        building_id = building_id_match.group(1)

        print(f"✅ 成功获取参数：")
        print(f"   - Token: {token}")
        print(f"   - Sub ID: {sub_id}")
        print(f"   - Building ID: {building_id}")
        return token, sub_id, building_id

    except requests.exceptions.RequestException as e:
        print(f"错误：获取选房参数时网络请求失败: {e}")
        return None, None, None
    except (AttributeError, TypeError) as e:
        print(f"错误：解析选房参数页面失败: {e}")
        return None, None, None


def get_and_display_rooms(session, building_id, sub_id):
    """
    Step 3 (Part 2): 获取并展示可选的房间和床位 (支持多楼层)
    """
    try:
        print("\nStep 3: 正在获取可选房间列表...")

        # 先请求一次，获取 floorSelect 列表
        url = f"{FLOOR_SHOW_API_URL}?buildingId={building_id}&subId={sub_id}&floorId=&fjcx=&price=&bedsType=&air=&habitStr=&enlink-vpn"
        response = session.post(url)
        response.raise_for_status()
        log_request(response)

        data = response.json()
        if data.get('status') != 'success':
            print(f"错误：API返回错误信息 - {data.get('message', '未知错误')}")
            return None

        # 可能有多个楼层
        floor_select_list = data.get('data', {}).get('floorSelect', [])
        if not floor_select_list:
            print("未找到任何楼层信息。")
            return None

        all_rooms_data = []

        # 遍历每一个楼层，重新请求接口获取该楼层的详细房间数据
        for floor_sel in floor_select_list:
            floor_id = floor_sel.get("floorId")
            floor_no = floor_sel.get("no", "未知楼层")

            print(f"\n--- 正在获取楼层 {floor_no} 的房间信息 ---")
            floor_url = f"{FLOOR_SHOW_API_URL}?buildingId={building_id}&subId={sub_id}&floorId={floor_id}&fjcx=&price=&bedsType=&air=&habitStr=&enlink-vpn"
            floor_resp = session.post(floor_url)
            floor_resp.raise_for_status()
            log_request(floor_resp)

            floor_data = floor_resp.json().get('data', {}).get('floor', [])
            if floor_data:
                all_rooms_data.extend(floor_data)

        if not all_rooms_data:
            print("未找到任何房间信息。")
            return None

        print("\n--- 可选房间和床位列表 ---")
        available_beds_found = False
        for floor in all_rooms_data:
            floor_name = floor.get("no", "未知楼层")
            for room in floor.get('room', []):
                room_name = room.get('name', '未知房间')
                available_beds_in_room = []
                for bed in room.get('bed', []):
                    if bed.get('choose') == 'notCho':  # 可选
                        available_beds_in_room.append({
                            'bed_name': bed.get('name'),
                            'price': bed.get('bedPrice')
                        })
                if available_beds_in_room:
                    available_beds_found = True
                    print(f"🏠 楼层 {floor_name} - 房间: {room_name}")
                    for bed_info in available_beds_in_room:
                        print(f"   - 🛏️ 床号: {bed_info['bed_name']}, 💰 费用: {bed_info['price']}")

        if not available_beds_found:
            print("所有房间都已选满，没有找到可选床位。")
            return None

        print("--------------------------")
        return all_rooms_data

    except requests.exceptions.RequestException as e:
        print(f"错误：获取房间列表时网络请求失败: {e}")
        return None
    except json.JSONDecodeError:
        print("错误：解析房间列表API的响应失败，不是有效的JSON格式。")
        return None


def select_bed(session, all_rooms_data, params):
    """
    Step 4: 根据用户输入选择床位
    """
    try:
        print("\nStep 4: 请输入你想选择的房间和床位。")
        target_room_name = input("请输入完整的房间号 (例如: 沁园36-314): ").strip()
        target_bed_name = input("请输入床位号 (例如: 1): ").strip()

        target_room_id = None
        target_bed_id = None
        target_floor_id = None

        # 遍历数据找到用户输入对应的ID
        for floor in all_rooms_data:
            for room in floor.get('room', []):
                if room.get('name') == target_room_name:
                    for bed in room.get('bed', []):
                        if bed.get('name') == target_bed_name and bed.get('choose') == 'notCho':
                            target_room_id = room.get('id')
                            target_bed_id = bed.get('id')
                            target_floor_id = room.get('floorId')
                            break
                    if target_bed_id:
                        break
            if target_bed_id:
                break
        
        if not all([target_room_id, target_bed_id, target_floor_id]):
            print("错误：未找到你输入的房间和床位，或者该床位不可选。请检查输入是否正确。")
            return

        print("\n确认选房信息...")
        print(f"  - 房间号: {target_room_name} (ID: {target_room_id})")
        print(f"  - 床位号: {target_bed_name} (ID: {target_bed_id})")
        post_data = {
            'roomId': target_room_id,
            'bedId': target_bed_id,
            'floorId': target_floor_id,
            'token': params['token'],
            'buildingId': params['buildingId'],
            'studentName': params['studentName'],
            'deptName': params['deptName'],
            'subId': params['subId']
        }
        print(post_data)
        
        confirm = input("是否确认提交选择？(y/n): ").strip().lower()
        if confirm != 'y':
            print("操作已取消。")
            return

        # 准备POST请求的数据


        print("正在提交选房请求...")
        response = session.post(SAVE_CHOSEN_API_URL, data=post_data)
        response.raise_for_status()
        log_request(response)
        
        result = response.json()
        if result.get('status') == 'success':
            print(f"🎉 恭喜！选房成功！服务器消息: {result.get('data')}")
        else:
            print(f"😥 选房失败。服务器消息: {result.get('message', '未知错误')}")

    except requests.exceptions.RequestException as e:
        print(f"错误：提交选房请求时网络请求失败: {e}")
    except json.JSONDecodeError:
        print("错误：解析选房结果响应失败，不是有效的JSON格式。")
    except Exception as e:
        print(f"发生未知错误: {e}")


def main():
    """主函数，组织脚本执行流程"""
    parser = argparse.ArgumentParser(description="NUIST宿舍自动选择脚本")
    parser.add_argument(
        '-ck', '--cookies', 
        required=True, 
        help='必需参数，用于身份验证的Cookie字符串'
    )
    args = parser.parse_args()

    # 创建一个会话对象，它会自动处理cookies
    session = requests.Session()
    
    # 设置请求头，模拟浏览器
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Cookie': args.cookies
    })

    # --- 开始执行流程 ---
    name, college = get_personal_info(session)
    if not name or not college:
        return # 如果获取个人信息失败，则终止脚本

    token, sub_id, building_id = get_selection_params(session, name, college)
    if not all([token, sub_id, building_id]):
        return

    all_rooms_data = get_and_display_rooms(session, building_id, sub_id)
    if not all_rooms_data:
        return

    # 组合所有需要的参数
    selection_final_params = {
        'token': token,
        'buildingId': building_id,
        'studentName': name,
        'subId': sub_id,
        'deptName': college
    }
    
    select_bed(session, all_rooms_data, selection_final_params)


if __name__ == '__main__':
    main()
