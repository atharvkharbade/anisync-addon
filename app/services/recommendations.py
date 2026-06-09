import logging
import random
import datetime
import asyncio
import json
from typing import Optional

import httpx

from app.services.db import db, get_user, store_user
from app.services.http import get_client
from app.api import mal as mal_api
from app.api import anilist as anilist_api
from app.lib.id_resolver import resolve_mal_to_kitsu, resolve_anilist_to_kitsu, resolve

logger = logging.getLogger(__name__)
recommendations_cache_collection = db.get_collection("recommendations_cache")

currently_updating_users = set()

# Popular anime fallback data
# Popular anime fallback data
POPULAR_FALLBACKS = [
    {
        "id": "mal:52991",
        "type": "series",
        "name": "Sousou no Frieren",
        "poster": "https://cdn.myanimelist.net/images/anime/1015/138006l.jpg",
        "description": "During their decade-long quest to defeat the Demon King, the members of the hero's party\u2014Himmel himself, the priest Heiter, the dwarf warrior Eisen, a..."
    },
    {
        "id": "mal:61469",
        "type": "series",
        "name": "Steel Ball Run: JoJo no Kimyou na Bouken",
        "poster": "https://cdn.myanimelist.net/images/anime/1448/154111l.jpg",
        "description": "In the American Old West, the world's greatest race is about to begin. Thousands line up in San Diego to travel over six thousand kilometers for a cha..."
    },
    {
        "id": "mal:5114",
        "type": "series",
        "name": "Fullmetal Alchemist: Brotherhood",
        "poster": "https://cdn.myanimelist.net/images/anime/1208/94745l.jpg",
        "description": "After a horrific alchemy experiment goes wrong in the Elric household, brothers Edward and Alphonse are left in a catastrophic new reality. Ignoring t..."
    },
    {
        "id": "mal:57555",
        "type": "movie",
        "name": "Chainsaw Man Movie: Reze-hen",
        "poster": "https://cdn.myanimelist.net/images/anime/1763/150638l.jpg",
        "description": "Despite the immediate challenges following becoming a devil hunter with the Public Safety Bureau, Denji has quickly adapted to his new life and respon..."
    },
    {
        "id": "mal:9253",
        "type": "series",
        "name": "Steins;Gate",
        "poster": "https://cdn.myanimelist.net/images/anime/1935/127974l.jpg",
        "description": "Eccentric scientist Rintarou Okabe has a never-ending thirst for scientific exploration. Together with his ditzy but well-meaning friend Mayuri Shiina..."
    },
    {
        "id": "mal:38524",
        "type": "series",
        "name": "Shingeki no Kyojin Season 3 Part 2",
        "poster": "https://cdn.myanimelist.net/images/anime/1517/100633l.jpg",
        "description": "Seeking to restore humanity's diminishing hope, the Survey Corps embark on a mission to retake Wall Maria, where the battle against the merciless \"Tit..."
    },
    {
        "id": "mal:28977",
        "type": "series",
        "name": "Gintama\u00b0",
        "poster": "https://cdn.myanimelist.net/images/anime/3/72078l.jpg",
        "description": "Gintoki, Shinpachi, and Kagura return as the fun-loving but broke members of the Yorozuya team! Living in an alternate-reality Edo, where swords are p..."
    },
    {
        "id": "mal:39486",
        "type": "movie",
        "name": "Gintama: The Final",
        "poster": "https://cdn.myanimelist.net/images/anime/1245/116760l.jpg",
        "description": "Two years have passed following the Tendoshuu's invasion of the O-Edo Central Terminal. Since then, the Yorozuya have gone their separate ways. Forese..."
    },
    {
        "id": "mal:11061",
        "type": "series",
        "name": "Hunter x Hunter (2011)",
        "poster": "https://cdn.myanimelist.net/images/anime/1337/99013l.jpg",
        "description": "Hunters devote themselves to accomplishing hazardous tasks, all from traversing the world's uncharted territories to locating rare items and monsters...."
    },
    {
        "id": "mal:9969",
        "type": "series",
        "name": "Gintama'",
        "poster": "https://cdn.myanimelist.net/images/anime/4/50361l.jpg",
        "description": "After a one-year hiatus, Shinpachi Shimura returns to Edo, only to stumble upon a shocking surprise: Gintoki and Kagura, his fellow Yorozuya members, ..."
    },
    {
        "id": "mal:15417",
        "type": "series",
        "name": "Gintama': Enchousen",
        "poster": "https://cdn.myanimelist.net/images/anime/1452/123686l.jpg",
        "description": "While Gintoki Sakata was away, the Yorozuya found themselves a new leader: Kintoki, Gintoki's golden-haired doppelganger. In order to regain his forme..."
    },
    {
        "id": "mal:820",
        "type": "series",
        "name": "Ginga Eiyuu Densetsu",
        "poster": "https://cdn.myanimelist.net/images/anime/1976/142016l.jpg",
        "description": "The 150-year-long stalemate between the two interstellar superpowers, the Galactic Empire and the Free Planets Alliance, comes to an end when a new ge..."
    },
    {
        "id": "mal:60022",
        "type": "series",
        "name": "One Piece Fan Letter",
        "poster": "https://cdn.myanimelist.net/images/anime/1455/146229l.jpg",
        "description": "Although the golden age of piracy is about to reach new heights, most people do not seek the glory of finding the elusive One Piece\u2014a treasure signify..."
    },
    {
        "id": "mal:34096",
        "type": "series",
        "name": "Gintama.",
        "poster": "https://cdn.myanimelist.net/images/anime/3/83528l.jpg",
        "description": "After joining the resistance against the bakufu, Gintoki and the gang are in hiding, along with Katsura and his Joui rebels. The Yorozuya is soon appr..."
    },
    {
        "id": "mal:41467",
        "type": "series",
        "name": "Bleach: Sennen Kessen-hen",
        "poster": "https://cdn.myanimelist.net/images/anime/1908/135431l.jpg",
        "description": "Substitute Soul Reaper Ichigo Kurosaki spends his days fighting against Hollows, dangerous evil spirits that threaten Karakura Town. Ichigo carries ou..."
    },
    {
        "id": "mal:61316",
        "type": "series",
        "name": "Re:Zero kara Hajimeru Isekai Seikatsu 4th Season",
        "poster": "https://cdn.myanimelist.net/images/anime/1540/155824l.jpg",
        "description": "In the deadly battle at the Watergate City of Priestella, Subaru and his allies barely emerged victorious\u2014but their triumph came at a great cost. Thro..."
    },
    {
        "id": "mal:43608",
        "type": "series",
        "name": "Kaguya-sama wa Kokurasetai: Ultra Romantic",
        "poster": "https://cdn.myanimelist.net/images/anime/1160/122627l.jpg",
        "description": "The elite members of Shuchiin Academy's student council continue their competitive day-to-day antics. Council president Miyuki Shirogane clashes daily..."
    },
    {
        "id": "mal:42938",
        "type": "series",
        "name": "Fruits Basket: The Final",
        "poster": "https://cdn.myanimelist.net/images/anime/1085/114792l.jpg",
        "description": "Hundreds of years ago, the Chinese zodiac spirits and their god swore to stay together eternally. United by this promise, the possessed members of the..."
    },
    {
        "id": "mal:4181",
        "type": "series",
        "name": "Clannad: After Story",
        "poster": "https://cdn.myanimelist.net/images/anime/1299/110774l.jpg",
        "description": "Tomoya Okazaki and Nagisa Furukawa have graduated from high school, and together, they experience the emotional rollercoaster of growing up. Unable to..."
    },
    {
        "id": "mal:918",
        "type": "series",
        "name": "Gintama",
        "poster": "https://cdn.myanimelist.net/images/anime/10/73274l.jpg",
        "description": "Edo is a city that was home to the vigor and ambition of samurai across the country. However, following feudal Japan's surrender to powerful aliens kn..."
    },
    {
        "id": "mal:28851",
        "type": "movie",
        "name": "Koe no Katachi",
        "poster": "https://cdn.myanimelist.net/images/anime/1122/96435l.jpg",
        "description": "As a wild youth, elementary school student Shouya Ishida sought to beat boredom in the cruelest ways. When the deaf Shouko Nishimiya transfers into hi..."
    },
    {
        "id": "mal:2904",
        "type": "series",
        "name": "Code Geass: Hangyaku no Lelouch R2",
        "poster": "https://cdn.myanimelist.net/images/anime/1088/135089l.jpg",
        "description": "One year has passed since the Black Rebellion, a failed uprising against the Holy Britannian Empire led by the masked vigilante Zero, who is now missi..."
    },
    {
        "id": "mal:58514",
        "type": "series",
        "name": "Kusuriya no Hitorigoto 2nd Season",
        "poster": "https://cdn.myanimelist.net/images/anime/1025/147458l.jpg",
        "description": "Using her wit and vast knowledge of medicines and poisons alike, Maomao played a pivotal role in solving a series of mysteries and conspiracies that p..."
    },
    {
        "id": "mal:35180",
        "type": "series",
        "name": "3-gatsu no Lion 2nd Season",
        "poster": "https://cdn.myanimelist.net/images/anime/3/88469l.jpg",
        "description": "Now in his second year of high school, Rei Kiriyama continues pushing through his struggles in the professional shogi world as well as his personal li..."
    },
    {
        "id": "mal:15335",
        "type": "movie",
        "name": "Gintama Movie 2: Kanketsu-hen - Yorozuya yo Eien Nare",
        "poster": "https://cdn.myanimelist.net/images/anime/10/51723l.jpg",
        "description": "When Gintoki apprehends a movie pirate at a premiere, he checks the camera's footage and finds himself transported to a bleak, post-apocalyptic versio..."
    },
    {
        "id": "mal:19",
        "type": "series",
        "name": "Monster",
        "poster": "https://cdn.myanimelist.net/images/anime/10/18793l.jpg",
        "description": "Dr. Kenzou Tenma, an elite neurosurgeon recently engaged to his hospital director's daughter, is well on his way to ascending the hospital hierarchy. ..."
    },
    {
        "id": "mal:59978",
        "type": "series",
        "name": "Sousou no Frieren 2nd Season",
        "poster": "https://cdn.myanimelist.net/images/anime/1921/154528l.jpg",
        "description": "Following the First-Class Mage Exam, the trio\u2014elven mage Frieren, warrior Stark, and first-class mage Fern\u2014gains access to the dangerous Northern Plat..."
    },
    {
        "id": "mal:37491",
        "type": "series",
        "name": "Gintama. Shirogane no Tamashii-hen - Kouhan-sen",
        "poster": "https://cdn.myanimelist.net/images/anime/1776/96566l.jpg",
        "description": "Following the temporary retreat of the Altana Liberation Army from the Kabuki District, the state of the war has seemingly improved. However, as the O..."
    },
    {
        "id": "mal:51535",
        "type": "series",
        "name": "Shingeki no Kyojin: The Final Season - Kanketsu-hen",
        "poster": "https://cdn.myanimelist.net/images/anime/1279/131078l.jpg",
        "description": "In the wake of Eren Yeager's cataclysmic actions, his friends and former enemies form an alliance against his genocidal rampage. Though once bitter fo..."
    },
    {
        "id": "mal:35247",
        "type": "series",
        "name": "Owarimonogatari 2nd Season",
        "poster": "https://cdn.myanimelist.net/images/anime/6/87322l.jpg",
        "description": "Following an encounter with oddity specialist Izuko Gaen, third-year high school student Koyomi Araragi wakes up in a strange, deserted void only to b..."
    },
    {
        "id": "mal:54492",
        "type": "series",
        "name": "Kusuriya no Hitorigoto",
        "poster": "https://cdn.myanimelist.net/images/anime/1708/138033l.jpg",
        "description": "Maomao, an apothecary's daughter, has been plucked from her peaceful life and sold to the lowest echelons of the imperial court. Now merely a maid, Ma..."
    },
    {
        "id": "mal:40682",
        "type": "series",
        "name": "Kingdom 3rd Season",
        "poster": "https://cdn.myanimelist.net/images/anime/1443/111830l.jpg",
        "description": "Following the successful Sanyou campaign, the Qin army, including 1,000-Man Commander Xin, inches ever closer to fulfilling King Ying Zheng's dream of..."
    },
    {
        "id": "mal:59571",
        "type": "movie",
        "name": "Shingeki no Kyojin Movie: Kanketsu-hen - The Last Attack",
        "poster": "https://cdn.myanimelist.net/images/anime/1379/145452l.jpg",
        "description": "A compilation movie for Shingeki no Kyojin: The Final Season - Kanketsu-hen."
    },
    {
        "id": "mal:37987",
        "type": "movie",
        "name": "Violet Evergarden Movie",
        "poster": "https://cdn.myanimelist.net/images/anime/1825/110716l.jpg",
        "description": "Several years have passed since the end of The Great War. As the radio tower in Leidenschaftlich continues to be built, telephones will soon become mo..."
    },
    {
        "id": "mal:49387",
        "type": "series",
        "name": "Vinland Saga Season 2",
        "poster": "https://cdn.myanimelist.net/images/anime/1170/124312l.jpg",
        "description": "After his father's death and the destruction of his village at the hands of English raiders, Einar wishes for a peaceful life with his family on their..."
    },
    {
        "id": "mal:32281",
        "type": "movie",
        "name": "Kimi no Na wa.",
        "poster": "https://cdn.myanimelist.net/images/anime/5/87048l.jpg",
        "description": "Mitsuha Miyamizu, a high school girl, yearns to live the life of a boy in the bustling city of Tokyo\u2014a dream that stands in stark contrast to her pres..."
    },
    {
        "id": "mal:36838",
        "type": "series",
        "name": "Gintama. Shirogane no Tamashii-hen",
        "poster": "https://cdn.myanimelist.net/images/anime/12/89603l.jpg",
        "description": "After the fierce battle on Rakuyou, the untold past and true goal of the immortal Naraku leader, Utsuro, are finally revealed. By corrupting the Altan..."
    },
    {
        "id": "mal:2921",
        "type": "series",
        "name": "Ashita no Joe 2",
        "poster": "https://cdn.myanimelist.net/images/anime/3/45028l.jpg",
        "description": "Yabuki Joe is left downhearted and hopeless after a certain tragic event. In attempt to put the past behind him, Joe leaves the gym behind and begins ..."
    },
    {
        "id": "mal:40028",
        "type": "series",
        "name": "Shingeki no Kyojin: The Final Season",
        "poster": "https://cdn.myanimelist.net/images/anime/1000/110531l.jpg",
        "description": "Gabi Braun and Falco Grice have been training their entire lives to inherit one of the seven Titans under Marley's control and aid their nation in era..."
    },
    {
        "id": "mal:37510",
        "type": "series",
        "name": "Mob Psycho 100 II",
        "poster": "https://cdn.myanimelist.net/images/anime/1918/96303l.jpg",
        "description": "Shigeo \"Mob\" Kageyama is now maturing and understanding his role as a supernatural psychic that has the power to drastically affect the livelihood of ..."
    },
    {
        "id": "mal:31758",
        "type": "movie",
        "name": "Kizumonogatari III: Reiketsu-hen",
        "poster": "https://cdn.myanimelist.net/images/anime/1084/112813l.jpg",
        "description": "After helping revive the legendary vampire Kiss-shot Acerola-orion Heart-under-blade, Koyomi Araragi has become a vampire himself and her servant. Kis..."
    },
    {
        "id": "mal:37521",
        "type": "series",
        "name": "Vinland Saga",
        "poster": "https://cdn.myanimelist.net/images/anime/1500/103005l.jpg",
        "description": "Young Thorfinn grew up listening to the stories of old sailors that had traveled the ocean and reached the place of legend, Vinland. It's said to be w..."
    },
    {
        "id": "mal:263",
        "type": "series",
        "name": "Hajime no Ippo",
        "poster": "https://cdn.myanimelist.net/images/anime/4/86334l.jpg",
        "description": "In his father's absence, teenager Ippo Makunouchi works hard to help his mother run her fishing boat rental business. Ippo's timid nature, his lack of..."
    },
    {
        "id": "mal:32935",
        "type": "series",
        "name": "Haikyuu!! Karasuno Koukou vs. Shiratorizawa Gakuen Koukou",
        "poster": "https://cdn.myanimelist.net/images/anime/7/81992l.jpg",
        "description": "After the victory against Aoba Jousai High, Karasuno High School, once called \u201ca fallen powerhouse, a crow that can\u2019t fly,\u201d has finally reached the cl..."
    },
    {
        "id": "mal:199",
        "type": "movie",
        "name": "Sen to Chihiro no Kamikakushi",
        "poster": "https://cdn.myanimelist.net/images/anime/6/79597l.jpg",
        "description": "Stubborn, spoiled, and na\u00efve, 10-year-old Chihiro Ogino is less than pleased when she and her parents discover an abandoned amusement park on the way ..."
    },
    {
        "id": "mal:48583",
        "type": "series",
        "name": "Shingeki no Kyojin: The Final Season Part 2",
        "poster": "https://cdn.myanimelist.net/images/anime/1948/120625l.jpg",
        "description": "Turning against his former allies and enemies alike, Eren Yeager sets a disastrous plan in motion. Under the guidance of the Beast Titan, Zeke, Eren t..."
    },
    {
        "id": "mal:17074",
        "type": "series",
        "name": "Monogatari Series: Second Season",
        "poster": "https://cdn.myanimelist.net/images/anime/1807/121534l.jpg",
        "description": "Apparitions, oddities, and gods continue to manifest around Koyomi Araragi and his close-knit group of friends: Tsubasa Hanekawa, the group's modest g..."
    },
    {
        "id": "mal:60489",
        "type": "series",
        "name": "Takopii no Genzai",
        "poster": "https://cdn.myanimelist.net/images/anime/1182/149879l.jpg",
        "description": "A squid-like creature, known as a Happian, leaves his home planet with the desire to spread happiness across the universe. He lands on Earth, but quic..."
    },
    {
        "id": "mal:1",
        "type": "series",
        "name": "Cowboy Bebop",
        "poster": "https://cdn.myanimelist.net/images/anime/4/19644l.jpg",
        "description": "Crime is timeless. By the year 2071, humanity has expanded across the galaxy, filling the surface of other planets with settlements like those on Eart..."
    },
    {
        "id": "mal:58788",
        "type": "series",
        "name": "Ikoku Nikki",
        "poster": "https://cdn.myanimelist.net/images/anime/1791/154233l.jpg",
        "description": "Thirty-five-year-old novelist Makio Koudai never had a good relationship with her older sister Minori, who always berated her for being different. Due..."
    },
    {
        "id": "mal:39894",
        "type": "series",
        "name": "Hibike! Euphonium 3",
        "poster": "https://cdn.myanimelist.net/images/anime/1216/142086l.jpg",
        "description": "With the ensemble contest behind them, the members of the Kitauji High School concert band now aim to win a gold medal at the national competition. Fo..."
    },
    {
        "id": "mal:47917",
        "type": "series",
        "name": "Bocchi the Rock!",
        "poster": "https://cdn.myanimelist.net/images/anime/1448/127956l.jpg",
        "description": "Yearning to make friends and perform live with a band, lonely and socially anxious Hitori \"Bocchi\" Gotou devotes her time to playing the guitar. On a ..."
    },
    {
        "id": "mal:50160",
        "type": "series",
        "name": "Kingdom 4th Season",
        "poster": "https://cdn.myanimelist.net/images/anime/1566/122794l.jpg",
        "description": "Following the conclusion of the large-scale coalition campaign, the entirety of China is in a state of economic recovery. The victor of the battle, th..."
    },
    {
        "id": "mal:21",
        "type": "series",
        "name": "One Piece",
        "poster": "https://cdn.myanimelist.net/images/anime/1244/138851l.jpg",
        "description": "Barely surviving in a barrel after passing through a terrible whirlpool at sea, carefree Monkey D. Luffy ends up aboard a ship under attack by fearsom..."
    },
    {
        "id": "mal:53223",
        "type": "series",
        "name": "Kingdom 5th Season",
        "poster": "https://cdn.myanimelist.net/images/anime/1050/139641l.jpg",
        "description": "Fifth season of Kingdom."
    },
    {
        "id": "mal:52215",
        "type": "series",
        "name": "Chi. Chikyuu no Undou ni Tsuite",
        "poster": "https://cdn.myanimelist.net/images/anime/1749/145922l.jpg",
        "description": "Twelve-year-old prodigy Rafal believes in living rationally, so as to earn praise and respect from society while not being led astray by his emotions...."
    },
    {
        "id": "mal:51553",
        "type": "series",
        "name": "Tongari Boushi no Atelier",
        "poster": "https://cdn.myanimelist.net/images/anime/1726/155542l.jpg",
        "description": "In a world where witches wield breathtaking magic, Coco, coming from a humble background, often wishes she were born one herself. After all, the secre..."
    },
    {
        "id": "mal:24701",
        "type": "series",
        "name": "Mushishi Zoku Shou 2nd Season",
        "poster": "https://cdn.myanimelist.net/images/anime/9/68095l.jpg",
        "description": "Ghostly, primordial beings known as Mushi continue to cause mysterious changes in the lives of humans. The travelling Mushishi, Ginko, persists in try..."
    },
    {
        "id": "mal:50172",
        "type": "series",
        "name": "Mob Psycho 100 III",
        "poster": "https://cdn.myanimelist.net/images/anime/1228/125011l.jpg",
        "description": "After foiling a world-threatening plot, Shigeo \"Mob\" Kageyama returns to tackle the more exhausting aspects of his mundane life\u2014starting with filling ..."
    },
    {
        "id": "mal:48569",
        "type": "series",
        "name": "86 Part 2",
        "poster": "https://cdn.myanimelist.net/images/anime/1321/117508l.jpg",
        "description": "The disappearance of the Spearhead Squadron beyond the horizon does little to hide the intensity of the Republic of San Magnolia's endless propaganda...."
    },
    {
        "id": "mal:60098",
        "type": "series",
        "name": "Boku no Hero Academia: Final Season",
        "poster": "https://cdn.myanimelist.net/images/anime/1959/151055l.jpg",
        "description": "The final stages of an all-out war between heroes and villains unfold as the world watches its symbols of peace and destruction collide. When All Migh..."
    },
    {
        "id": "mal:55016",
        "type": "series",
        "name": "Idol",
        "poster": "https://cdn.myanimelist.net/images/anime/1921/135489l.jpg",
        "description": "Music video for the song Idol by YOASOBI. The song was used as the opening theme of the anime [Oshi no Ko]."
    },
    {
        "id": "mal:52198",
        "type": "movie",
        "name": "Kaguya-sama wa Kokurasetai: First Kiss wa Owaranai",
        "poster": "https://cdn.myanimelist.net/images/anime/1670/130060l.jpg",
        "description": "After their first kiss, Kaguya Shinomiya and Miyuki Shirogane are left unsure where their relationship stands. The troubling uncertainty of whether th..."
    },
    {
        "id": "mal:1575",
        "type": "series",
        "name": "Code Geass: Hangyaku no Lelouch",
        "poster": "https://cdn.myanimelist.net/images/anime/1032/135088l.jpg",
        "description": "In the year 2010, the Holy Empire of Britannia is establishing itself as a dominant military nation, starting with the conquest of Japan. Renamed to A..."
    },
    {
        "id": "mal:60058",
        "type": "series",
        "name": "[Oshi no Ko] 3rd Season",
        "poster": "https://cdn.myanimelist.net/images/anime/1979/153329l.jpg",
        "description": "Satisfied with his investigation of Lala Lai Theatrical Company, Aquamarine \"Aqua\" Hoshino shifts his focus from revenge to career growth and becomes ..."
    },
    {
        "id": "mal:53998",
        "type": "series",
        "name": "Bleach: Sennen Kessen-hen - Ketsubetsu-tan",
        "poster": "https://cdn.myanimelist.net/images/anime/1164/138058l.jpg",
        "description": "After a brutal surprise attack by the forces of Quincy King Yhwach, the resident Reapers of the Soul Society lick their wounds and mourn their losses...."
    },
    {
        "id": "mal:33095",
        "type": "series",
        "name": "Shouwa Genroku Rakugo Shinjuu: Sukeroku Futatabi-hen",
        "poster": "https://cdn.myanimelist.net/images/anime/1493/124765l.jpg",
        "description": "Even after having risen to the utmost rank of shin'uchi, Yotarou struggles to find his own identity in the world of rakugo. Caught between his master'..."
    },
    {
        "id": "mal:45649",
        "type": "movie",
        "name": "The First Slam Dunk",
        "poster": "https://cdn.myanimelist.net/images/anime/1745/129284l.jpg",
        "description": "Shohoku's \"speedster\" and point guard, Ryouta Miyagi, always plays with brains and lightning speed, running circles around his opponents while feignin..."
    },
    {
        "id": "mal:44074",
        "type": "series",
        "name": "Shiguang Dailiren",
        "poster": "https://cdn.myanimelist.net/images/anime/1135/114867l.jpg",
        "description": "It is said that a picture is worth a thousand words. In this case, it holds an infinite amount of secrets. These are secrets that only Cheng Xiaoshi a..."
    },
    {
        "id": "mal:51009",
        "type": "series",
        "name": "Jujutsu Kaisen 2nd Season",
        "poster": "https://cdn.myanimelist.net/images/anime/1792/138022l.jpg",
        "description": "The year is 2006, and the halls of Tokyo Prefectural Jujutsu High School echo with the endless bickering and intense debate between two inseparable be..."
    },
    {
        "id": "mal:55690",
        "type": "series",
        "name": "Boku no Kokoro no Yabai Yatsu 2nd Season",
        "poster": "https://cdn.myanimelist.net/images/anime/1643/138581l.jpg",
        "description": "After an eventful winter break, Kyoutarou Ichikawa and Anna Yamada reunite with a stronger bond. They continue to grow in their own ways, with Yamada ..."
    },
    {
        "id": "mal:33352",
        "type": "series",
        "name": "Violet Evergarden",
        "poster": "https://cdn.myanimelist.net/images/anime/1795/95088l.jpg",
        "description": "The Great War finally came to an end after four long years of conflict; fractured in two, the continent of Telesis slowly began to flourish once again..."
    },
    {
        "id": "mal:44",
        "type": "series",
        "name": "Rurouni Kenshin: Meiji Kenkaku Romantan - Tsuioku-hen",
        "poster": "https://cdn.myanimelist.net/images/anime/1656/137618l.jpg",
        "description": "When mankind's savagery surpasses his fear of death, there is little hope for those who wish to live honest lives. Beneath a full moon, a young boy wi..."
    },
    {
        "id": "mal:47778",
        "type": "series",
        "name": "Kimetsu no Yaiba: Yuukaku-hen",
        "poster": "https://cdn.myanimelist.net/images/anime/1908/120036l.jpg",
        "description": "The devastation of the Mugen Train incident still weighs heavily on the members of the Demon Slayer Corps. Despite being given time to recover, life m..."
    },
    {
        "id": "mal:53447",
        "type": "series",
        "name": "Tu Bian Yingxiong X",
        "poster": "https://cdn.myanimelist.net/images/anime/1492/150628l.jpg",
        "description": "This is a world where heroes are created by people's trust, and the hero who has received the most trust is known as \"X.\" In this world, people's trus..."
    }
]


def clean_html(text: str) -> str:
    if not text:
        return ""
    import re
    # Strip HTML tags
    clean = re.sub(r'<[^<]+?>', '', text)
    # Decode common HTML entities if any
    clean = clean.replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&apos;", "'")
    return clean.strip()


def is_proper_anime(title: str) -> bool:
    if not title:
        return True
    t_lower = title.lower()
    
    # Exclude obvious shorts, chibi series, recaps, side stories, and specials by keywords
    excl_keywords = [
        "break time", "kyuukei jikan", "chibi", "petit", "mini-anime", "mini anime", 
        "character theater", "chara gekijou", "picture drama", "recap", "summary", 
        "special episode", "pv", "trailer", "commercial", "short anime", "web short",
        "spin-off", "spinoff", "bonus", "audio commentary", "side story", "side stories",
        "junior high", "ple ple pleiades", "chara-gekijou", "chara gekijou"
    ]
    
    for kw in excl_keywords:
        if kw == "ona":
            import re
            if re.search(r'\bona\b', t_lower):
                return False
        elif kw == "ova":
            import re
            if re.search(r'\bova\b', t_lower):
                return False
        elif kw in t_lower:
            return False
            
    return True



async def get_mal_recommendations_for_id(token: str, mal_id: str) -> list[dict]:
    client = get_client()
    url = f"https://api.myanimelist.net/v2/anime/{mal_id}"
    # Fetch start_season, genres, media_type, popularity, mean, status to enforce user preferences in recommendations
    params = {"fields": "recommendations{node{id,title,main_picture,genres,start_season,media_type,popularity,mean,synopsis,average_episode_duration,status}}"}
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = await client.get(url, params=params, headers=headers, timeout=10)
        if resp.status_code == 200:
            return resp.json().get("recommendations", [])
    except Exception as e:
        logger.warning("Failed to fetch MAL recommendations for MAL ID %s: %s", mal_id, e)
    return []


async def get_anilist_recommendations_bulk(token: str, anilist_ids: list[int]) -> list[dict]:
    if not anilist_ids:
        return []
    query = """
    query ($mediaIds: [Int]) {
      Page(page: 1, perPage: 50) {
        recommendations(mediaId_in: $mediaIds, sort: RATING_DESC) {
          rating
          media {
            id
          }
          mediaRecommendation {
            id
            idMal
            status
            title {
              english
              romaji
              userPreferred
            }
            coverImage {
              large
              medium
            }
            startDate {
              year
            }
            genres
            format
            duration
            popularity
            averageScore
            description
          }
        }
      }
    }
    """
    try:
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        payload = {"query": query, "variables": {"mediaIds": anilist_ids}}
        client = get_client()
        resp = await client.post("https://graphql.anilist.co", json=payload, headers=headers, timeout=10)
        if resp.status_code == 200:
            return resp.json().get("data", {}).get("Page", {}).get("recommendations", [])
    except Exception as e:
        logger.warning("Failed bulk AniList recommendations query: %s", e)
    return []


async def resolve_title_via_kitsu(title: str, rec_year_min: int = 1970, rec_year_max: int = 2026, rec_excluded_genres: list = None) -> Optional[dict]:
    url = "https://kitsu.io/api/edge/anime"
    params = {"filter[text]": title, "page[limit]": 1}
    headers = {
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json",
    }
    client = get_client()
    try:
        resp = await client.get(url, params=params, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            if data:
                item = data[0]
                kitsu_id = str(item["id"])
                attrs = item.get("attributes", {})
                
                # Check year bounds
                start_date = attrs.get("startDate")
                if start_date:
                    try:
                        k_year = int(start_date[:4])
                        if k_year < rec_year_min or k_year > rec_year_max:
                            return None
                    except ValueError:
                        pass
                
                subtype = (attrs.get("subtype") or "tv").lower()
                item_type = "movie" if subtype == "movie" else "series"
                titles = attrs.get("titles", {})
                canonical_title = attrs.get("canonicalTitle") or titles.get("en") or titles.get("en_jp") or title
                poster = attrs.get("posterImage", {}).get("large") or attrs.get("posterImage", {}).get("medium") or ""
                synopsis = attrs.get("synopsis", "")
                
                return {
                    "id": f"kitsu:{kitsu_id}",
                    "type": item_type,
                    "name": canonical_title,
                    "poster": poster,
                    "description": synopsis[:200] + "..." if len(synopsis) > 200 else synopsis
                }
    except Exception as e:
        logger.warning("Failed to resolve Gemini title '%s' via Kitsu: %s", title, e)
    return None


async def update_recommendations_cache(user_id: str, force: bool = False):
    if user_id in currently_updating_users:
        return
    currently_updating_users.add(user_id)
    try:
        await _update_recommendations_cache_impl(user_id, force)
    except Exception as e:
        logger.exception("Error updating recommendations for user %s: %s", user_id, e)
    finally:
        currently_updating_users.discard(user_id)


def normalize_user_status(status: Optional[str]) -> str:
    if not status:
        return "watching"
    s = status.lower()
    if s in ["watching", "current"]:
        return "watching"
    if s in ["completed"]:
        return "completed"
    if s in ["on_hold", "paused"]:
        return "on_hold"
    if s in ["dropped"]:
        return "dropped"
    if s in ["plan_to_watch", "planning"]:
        return "planning"
    return s


async def get_recommendations_for_seeds(
    seeds: list[dict],
    user: dict,
    watched_mal_ids: set,
    watched_anilist_ids: set,
    watched_titles: set,
    max_seeds: int = 15
) -> list[dict]:
    if not seeds:
        return []
    
    rec_language = user.get("rec_language", "en")
    rec_popularity = user.get("rec_popularity", "balanced")
    rec_year_min = user.get("rec_year_min", 1980)
    rec_year_max = user.get("rec_year_max", 2026)
    rec_excluded_movie_genres = user.get("rec_excluded_movie_genres", [])
    rec_excluded_series_genres = user.get("rec_excluded_series_genres", [])
    filter_watched = user.get("recommendations_filter_watched", True)
    
    rec_candidates = {}
    
    # 1. Fetch from AniList in bulk
    al_ids = [int(s["anilist_id"]) for s in seeds[:max_seeds] if s["anilist_id"]]
    al_recs = []
    anilist_id_to_title = {str(s["anilist_id"]): s["title"] for s in seeds if s.get("anilist_id")}
    if al_ids and user.get("anilist_token") and user.get("anilist_enabled", True):
        al_recs = await get_anilist_recommendations_bulk(user["anilist_token"], al_ids)
        
    for rec in al_recs:
        media = rec.get("mediaRecommendation")
        if not media:
            continue
        
        if media.get("status") == "NOT_YET_RELEASED":
            continue
        
        # Exclude OVA, SPECIAL, MUSIC, TV_SHORT and short durations (<= 5 minutes)
        m_format = media.get("format")
        duration = media.get("duration")
        if m_format in ["OVA", "SPECIAL", "MUSIC", "TV_SHORT"]:
            continue
        if duration is not None and duration <= 5:
            continue

        seed_media = rec.get("media") or {}
        seed_aid = str(seed_media.get("id")) if seed_media.get("id") else None
        seed_title = anilist_id_to_title.get(seed_aid) if seed_aid else None

        aid = str(media.get("id"))
        mid = str(media.get("idMal")) if media.get("idMal") else None
        
        # Watched filters
        if filter_watched:
            if aid in watched_anilist_ids or (mid and mid in watched_mal_ids):
                continue

        # Year filter
        year = media.get("startDate", {}).get("year")
        if year and (year < rec_year_min or year > rec_year_max):
            continue

        item_type = "movie" if m_format == "MOVIE" else "series"

        # Excluded genres filter
        genres = media.get("genres", []) or []
        excluded_genres = rec_excluded_movie_genres if item_type == "movie" else rec_excluded_series_genres
        if any(g in excluded_genres for g in genres):
            continue

        # Popularity filters
        pop_score = media.get("popularity") or 0
        avg_score = media.get("averageScore") or 0
        if rec_popularity == "mainstream":
            if pop_score < 25000:
                continue
        elif rec_popularity == "gems":
            if pop_score >= 25000 or avg_score < 73:
                continue

        # Choose title based on language
        title_pref = media.get("title", {})
        if rec_language == "ja":
            title = title_pref.get("romaji") or title_pref.get("userPreferred") or title_pref.get("english")
        else:
            title = title_pref.get("english") or title_pref.get("userPreferred") or title_pref.get("romaji")

        if filter_watched and title.lower() in watched_titles:
            continue

        if not is_proper_anime(title):
            continue

        poster = (media.get("coverImage") or {}).get("large") or (media.get("coverImage") or {}).get("medium") or ""
        syn = clean_html(media.get("description") or "")
        
        key = f"mal:{mid}" if mid else f"anilist:{aid}"
        if key not in rec_candidates:
            rec_candidates[key] = {
                "id": key,
                "type": item_type,
                "name": title,
                "poster": poster,
                "score": rec.get("rating", 1),
                "description": "Recommended based on your history.",
                "synopsis": syn,
                "inspired_by_titles": [seed_title] if seed_title else []
            }
        else:
            rec_candidates[key]["score"] += rec.get("rating", 1)
            if syn and not rec_candidates[key].get("synopsis"):
                rec_candidates[key]["synopsis"] = syn
            if seed_title and seed_title not in rec_candidates[key]["inspired_by_titles"]:
                rec_candidates[key]["inspired_by_titles"].append(seed_title)

    # 2. Fetch from MAL (limit to top 5 seeds for rate limits)
    mal_seed_shows = [s for s in seeds[:5] if s["mal_id"]]
    if mal_seed_shows and user.get("mal_access_token") and user.get("mal_enabled", True):
        tasks = [get_mal_recommendations_for_id(user["mal_access_token"], s["mal_id"]) for s in mal_seed_shows]
        mal_recs_lists = await asyncio.gather(*tasks)
        
        for s, rec_list in zip(mal_seed_shows, mal_recs_lists):
            seed_title = s["title"]
            for rec in rec_list:
                node = rec.get("node", {})
                mid = str(node.get("id"))
                title = node.get("title")

                if node.get("status") == "not_yet_aired":
                    continue

                # Exclude OVA, SPECIAL, MUSIC and short duration (<= 5 minutes / 300 seconds)
                m_type = node.get("media_type")
                duration = node.get("average_episode_duration")
                if m_type in ["ova", "special", "music"] or not is_proper_anime(title):
                    continue
                if duration is not None and duration <= 300:
                    continue

                # Watched filters
                if filter_watched:
                    if mid in watched_mal_ids:
                        continue
                    if title.lower() in watched_titles:
                        continue

                # Year filter
                year = node.get("start_season", {}).get("year")
                if year and (year < rec_year_min or year > rec_year_max):
                    continue

                item_type = "movie" if m_type == "movie" else "series"

                # Excluded genres filter
                genres = [g.get("name") for g in node.get("genres", []) if g.get("name")]
                excluded_genres = rec_excluded_movie_genres if item_type == "movie" else rec_excluded_series_genres
                if any(g in excluded_genres for g in genres):
                    continue

                # Popularity filters
                pop_rank = node.get("popularity")
                mean_score = node.get("mean")
                if rec_popularity == "mainstream":
                    if pop_rank and pop_rank > 1200:
                        continue
                elif rec_popularity == "gems":
                    if (pop_rank and pop_rank <= 1200) or (mean_score and mean_score < 7.3):
                        continue

                poster = node.get("main_picture", {}).get("large") or node.get("main_picture", {}).get("medium") or ""
                syn = clean_html(node.get("synopsis") or "")
                
                key = f"mal:{mid}"
                if key not in rec_candidates:
                    rec_candidates[key] = {
                        "id": key,
                        "type": item_type,
                        "name": title,
                        "poster": poster,
                        "score": rec.get("num_recommendations", 1),
                        "description": "Recommended based on your history.",
                        "synopsis": syn,
                        "inspired_by_titles": [seed_title]
                    }
                else:
                    rec_candidates[key]["score"] += rec.get("num_recommendations", 1)
                    if syn and not rec_candidates[key].get("synopsis"):
                        rec_candidates[key]["synopsis"] = syn
                    if seed_title not in rec_candidates[key]["inspired_by_titles"]:
                        rec_candidates[key]["inspired_by_titles"].append(seed_title)

    # 3. Kitsu Media Relationships (fetch sequels, prequels, spin-offs for up to 15 seeds)
    kitsu_seed_shows = [s for s in seeds[:15]]
    if kitsu_seed_shows:
        async def fetch_kitsu_relationships_for_seed(s):
            try:
                # 1. Resolve kitsu_id
                kitsu_id = None
                if s.get("mal_id"):
                    kitsu_id = await resolve_mal_to_kitsu(s["mal_id"])
                elif s.get("anilist_id"):
                    kitsu_id = await resolve_anilist_to_kitsu(s["anilist_id"])
                if not kitsu_id:
                    return s, []
                
                # 2. Fetch media-relationships from Kitsu
                url = f"https://kitsu.io/api/edge/anime/{kitsu_id}/media-relationships?include=destination"
                headers = {
                    "Accept": "application/vnd.api+json",
                    "Content-Type": "application/vnd.api+json",
                    "User-Agent": "Mozilla/5.0"
                }
                client = get_client()
                resp = await client.get(url, headers=headers, timeout=10)
                if resp.status_code != 200:
                    return s, []
                
                data = resp.json()
                included = data.get("included", [])
                related_items = []
                for item in included:
                    if item.get("type") == "anime":
                        kid = item.get("id")
                        attrs = item.get("attributes", {})
                        if not kid or not attrs:
                            continue
                        
                        # Exclude OVA, SPECIAL, MUSIC, and short duration (<= 5 minutes)
                        subtype = (attrs.get("subtype") or "tv").lower()
                        episode_length = attrs.get("episodeLength")
                        if subtype in ["ova", "special", "music"]:
                            continue
                        if episode_length is not None and episode_length <= 5:
                            continue

                        if attrs.get("status") in ["upcoming", "unreleased", "tba"]:
                            continue

                        start_date = attrs.get("startDate")
                        k_year = None
                        if start_date:
                            try:
                                k_year = int(start_date[:4])
                            except ValueError:
                                pass
                        
                        item_type = "movie" if subtype == "movie" else "series"
                        titles = attrs.get("titles", {})
                        title = attrs.get("canonicalTitle") or titles.get("en") or titles.get("en_jp")
                        if not is_proper_anime(title):
                            continue
                        poster = attrs.get("posterImage", {}).get("large") or attrs.get("posterImage", {}).get("medium") or ""
                        synopsis = attrs.get("synopsis", "")
                        
                        related_items.append({
                            "kitsu_id": kid,
                            "name": title,
                            "poster": poster,
                            "type": item_type,
                            "year": k_year,
                            "description": synopsis[:200] + "..." if len(synopsis) > 200 else synopsis
                        })
                return s, related_items
            except Exception as e:
                logger.warning("Kitsu relationship lookup failed for seed %s: %s", s.get("title"), e)
                return s, []

        kitsu_tasks = [fetch_kitsu_relationships_for_seed(s) for s in kitsu_seed_shows]
        kitsu_results = await asyncio.gather(*kitsu_tasks)
        
        for s, related_items in kitsu_results:
            seed_title = s["title"]
            for r_item in related_items:
                # Watched title filter first
                if filter_watched and r_item["name"].lower() in watched_titles:
                    continue
                
                # Resolve Kitsu ID to MAL/AniList IDs to do ID-based watched filtering
                mid, aid = await resolve(r_item["kitsu_id"])
                
                if filter_watched:
                    if (mid and mid in watched_mal_ids) or (aid and aid in watched_anilist_ids):
                        continue
                
                # Year filter
                year = r_item.get("year")
                if year and (year < rec_year_min or year > rec_year_max):
                    continue
                
                key = f"mal:{mid}" if mid else f"anilist:{aid}" if aid else f"kitsu:{r_item['kitsu_id']}"
                syn = clean_html(r_item.get("description") or "")
                
                # Add to candidates
                if key not in rec_candidates:
                    rec_candidates[key] = {
                        "id": key,
                        "type": r_item["type"],
                        "name": r_item["name"],
                        "poster": r_item["poster"],
                        "score": 10,  # Score boost for franchise expansions
                        "description": r_item["description"] or "Franchise sequel, prequel, or spin-off.",
                        "synopsis": syn,
                        "inspired_by_titles": [seed_title]
                    }
                else:
                    rec_candidates[key]["score"] += 10
                    if syn and not rec_candidates[key].get("synopsis"):
                        rec_candidates[key]["synopsis"] = syn
                    if seed_title not in rec_candidates[key]["inspired_by_titles"]:
                        rec_candidates[key]["inspired_by_titles"].append(seed_title)

    sorted_recs = sorted(rec_candidates.values(), key=lambda x: x["score"], reverse=True)
    for r in sorted_recs:
        r.pop("score", None)
    return sorted_recs


async def get_top_anime_by_genre(token: str, genre: str, sort: str = "POPULARITY_DESC") -> list[dict]:
    query = """
    query ($genre: String, $sort: [MediaSort]) {
      Page(page: 1, perPage: 50) {
        media(genre: $genre, type: ANIME, sort: $sort) {
          id
          idMal
          status
          title {
            english
            romaji
            userPreferred
          }
          coverImage {
            large
            medium
          }
          startDate {
            year
          }
          genres
          format
          duration
          popularity
          averageScore
          description
        }
      }
    }
    """
    try:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
            
        payload = {"query": query, "variables": {"genre": genre, "sort": [sort]}}
        client = get_client()
        resp = await client.post("https://graphql.anilist.co", json=payload, headers=headers, timeout=10)
        if resp.status_code == 200:
            return resp.json().get("data", {}).get("Page", {}).get("media", [])
    except Exception as e:
        logger.warning("Failed to fetch top anime for genre %s: %s", genre, e)
    return []


async def generate_genre_recommendations(
    genre: str,
    user: dict,
    watched_mal_ids: set,
    watched_anilist_ids: set,
    watched_titles: set
) -> list[dict]:
    rec_language = user.get("rec_language", "en")
    rec_popularity = user.get("rec_popularity", "balanced")
    rec_year_min = user.get("rec_year_min", 1980)
    rec_year_max = user.get("rec_year_max", 2026)
    rec_excluded_movie_genres = user.get("rec_excluded_movie_genres", [])
    rec_excluded_series_genres = user.get("rec_excluded_series_genres", [])
    filter_watched = user.get("recommendations_filter_watched", True)
    
    sort_order = "POPULARITY_DESC"
    if rec_popularity == "gems":
        sort_order = "SCORE_DESC"
        
    token = user.get("anilist_token")
    media_list = await get_top_anime_by_genre(token, genre, sort_order)
    
    recs = []
    for media in media_list:
        if not media:
            continue
        
        if media.get("status") == "NOT_YET_RELEASED":
            continue
        
        # Exclude OVA, SPECIAL, MUSIC, TV_SHORT and short durations (<= 5 minutes)
        m_format = media.get("format")
        duration = media.get("duration")
        if m_format in ["OVA", "SPECIAL", "MUSIC", "TV_SHORT"]:
            continue
        if duration is not None and duration <= 5:
            continue

        aid = str(media.get("id"))
        mid = str(media.get("idMal")) if media.get("idMal") else None
        
        # Watched filters
        if filter_watched:
            if aid in watched_anilist_ids or (mid and mid in watched_mal_ids):
                continue

        # Year filter
        year = media.get("startDate", {}).get("year")
        if year and (year < rec_year_min or year > rec_year_max):
            continue

        item_type = "movie" if m_format == "MOVIE" else "series"

        # Excluded genres filter
        genres = media.get("genres", []) or []
        excluded_genres = rec_excluded_movie_genres if item_type == "movie" else rec_excluded_series_genres
        if any(g in excluded_genres for g in genres):
            continue

        # Popularity filters
        pop_score = media.get("popularity") or 0
        avg_score = media.get("averageScore") or 0
        if rec_popularity == "mainstream":
            if pop_score < 25000:
                continue
        elif rec_popularity == "gems":
            if pop_score >= 25000 or avg_score < 73:
                continue

        # Choose title based on language
        title_pref = media.get("title", {})
        if rec_language == "ja":
            title = title_pref.get("romaji") or title_pref.get("userPreferred") or title_pref.get("english")
        else:
            title = title_pref.get("english") or title_pref.get("userPreferred") or title_pref.get("romaji")

        if filter_watched and title.lower() in watched_titles:
            continue

        if not is_proper_anime(title):
            continue

        poster = (media.get("coverImage") or {}).get("large") or (media.get("coverImage") or {}).get("medium") or ""
        
        key = f"mal:{mid}" if mid else f"anilist:{aid}"
        description = media.get("description", "")
        if description:
            import re
            description = re.sub('<[^<]+?>', '', description)
            if len(description) > 200:
                description = description[:200] + "..."
        else:
            description = f"Popular anime in {genre}."

        recs.append({
            "id": key,
            "type": item_type,
            "name": title,
            "poster": poster,
            "description": description
        })
    return recs



def select_weighted_seeds(pool, count):
    import random
    if len(pool) <= count:
        return pool
    selected = []
    pool_copy = list(pool)
    while len(selected) < count and pool_copy:
        weights = []
        for x in pool_copy:
            rating = x.get("rating") or 0
            if rating >= 9:
                w = 10
            elif 7 <= rating <= 8:
                w = 7
            elif 1 <= rating <= 6:
                w = 4
            else:  # unrated
                w = 5
            weights.append(w)
        choice = random.choices(pool_copy, weights=weights, k=1)[0]
        selected.append(choice)
        pool_copy.remove(choice)
    return selected


async def _update_recommendations_cache_impl(user_id: str, force: bool = False):
    user = get_user(user_id)
    if not user or not user.get("enable_recommendations", True):
        return
    fallbacks = get_popular_fallbacks()

    # Check if cache is fresh enough
    existing = recommendations_cache_collection.find_one({"uid": user_id})
    if existing and not force:
        last_updated = existing.get("last_updated")
        if last_updated and (datetime.datetime.utcnow() - last_updated) < datetime.timedelta(hours=24):
            return

    # Retrieve user preference filters
    rec_language = user.get("rec_language", "en")
    rec_popularity = user.get("rec_popularity", "balanced")
    rec_sorting_order = user.get("rec_sorting_order", "default")
    rec_year_min = user.get("rec_year_min", 1980)
    rec_year_max = user.get("rec_year_max", 2026)
    rec_excluded_movie_genres = user.get("rec_excluded_movie_genres", [])
    rec_excluded_series_genres = user.get("rec_excluded_series_genres", [])

    logger.info("Recalculating recommendations for user %s (Lang: %s, Pop: %s, Years: %s-%s, Excl Movies: %s, Excl Series: %s)...", 
                user_id, rec_language, rec_popularity, rec_year_min, rec_year_max, rec_excluded_movie_genres, rec_excluded_series_genres)

    # 1. Fetch watched history from both track managers
    mal_items = []
    if user.get("mal_access_token") and user.get("mal_enabled", True):
        try:
            res = await mal_api.get_user_anime_list(user["mal_access_token"], limit=100)
            mal_items = res.get("data", [])
        except Exception as e:
            logger.warning("Failed to fetch MAL user list: %s", e)

    anilist_items = []
    if user.get("anilist_token") and user.get("anilist_enabled", True):
        try:
            anilist_uid = user.get("anilist_id")
            if anilist_uid:
                anilist_uid = int(anilist_uid)
            else:
                viewer = await anilist_api.get_viewer(user["anilist_token"])
                anilist_uid = int(viewer["id"])
                user["anilist_id"] = str(anilist_uid)
                store_user(user)
            collection = await anilist_api.get_user_anime_list(user["anilist_token"], user_id=anilist_uid)
            for user_list in collection.get("lists", []):
                anilist_items.extend(user_list.get("entries", []))
        except Exception as e:
            logger.warning("Failed to fetch AniList user list: %s", e)

    # 2. Extract unique shows and filter watched lists
    merged_shows = {}
    
    for item in mal_items:
        node = item.get("node", {})
        title = node.get("title")
        mal_id = str(node.get("id"))
        list_status = item.get("list_status", {})
        status = normalize_user_status(list_status.get("status"))
        rating = list_status.get("score", 0)
        genres = [g.get("name") for g in node.get("genres", []) if g.get("name")]
        
        merged_shows[mal_id] = {
            "title": title,
            "mal_id": mal_id,
            "anilist_id": None,
            "status": status,
            "rating": rating,
            "genres": genres
        }

    for entry in anilist_items:
        media = entry.get("media", {})
        title = media.get("title", {}).get("english") or media.get("title", {}).get("userPreferred")
        anilist_id = str(media.get("id"))
        mal_id = str(media.get("idMal")) if media.get("idMal") else None
        status = normalize_user_status(entry.get("status"))
        rating = entry.get("score", 0)
        if rating > 10:
            rating = int(rating / 10)
        genres = media.get("genres", []) or []
            
        key = mal_id if mal_id else f"al_{anilist_id}"
        if key not in merged_shows:
            merged_shows[key] = {
                "title": title,
                "mal_id": mal_id,
                "anilist_id": anilist_id,
                "status": status,
                "rating": rating,
                "genres": genres
            }
        else:
            merged_shows[key]["anilist_id"] = anilist_id
            merged_shows[key]["rating"] = max(merged_shows[key]["rating"], rating)
            
            # Status merging: completed/watching/dropped/on_hold override planning
            old_status = merged_shows[key]["status"]
            if old_status == "planning" and status != "planning":
                merged_shows[key]["status"] = status
            elif old_status != "completed" and status == "completed":
                merged_shows[key]["status"] = "completed"
                
            # Merge genres
            old_genres = merged_shows[key].get("genres", [])
            for g in genres:
                if g not in old_genres:
                    old_genres.append(g)
            merged_shows[key]["genres"] = old_genres

    # Watched sets for filtering
    watched_mal_ids = set()
    watched_anilist_ids = set()
    watched_titles = set()
    
    for show in merged_shows.values():
        if show["status"] == "planning":
            continue
            
        if show["mal_id"]:
            watched_mal_ids.add(str(show["mal_id"]))
        if show["anilist_id"]:
            watched_anilist_ids.add(str(show["anilist_id"]))
        if show["title"]:
            watched_titles.add(show["title"].lower())

    # Bulk-resolve IDs from fribb_mappings and id_cache to ensure complete cross-tracker filtering
    raw_mal_ids = list(watched_mal_ids)
    raw_al_ids = list(watched_anilist_ids)
    if raw_mal_ids or raw_al_ids:
        # Query fribb_mappings
        fribb_query = []
        if raw_mal_ids:
            fribb_query.append({"mal_id": {"$in": raw_mal_ids}})
        if raw_al_ids:
            fribb_query.append({"anilist_id": {"$in": raw_al_ids}})
        if fribb_query:
            try:
                for doc in db.fribb_mappings.find({"$or": fribb_query}):
                    m_id = doc.get("mal_id")
                    a_id = doc.get("anilist_id")
                    if m_id:
                        watched_mal_ids.add(str(m_id))
                    if a_id:
                        watched_anilist_ids.add(str(a_id))
            except Exception as e:
                logger.warning("Failed to bulk query fribb_mappings for ID resolving: %s", e)
                
        # Query id_cache
        cache_query = []
        if raw_mal_ids:
            cache_query.append({"mal_id": {"$in": raw_mal_ids}})
        if raw_al_ids:
            cache_query.append({"anilist_id": {"$in": raw_al_ids}})
        if cache_query:
            try:
                for doc in db.get_collection("id_cache").find({"$or": cache_query}):
                    m_id = doc.get("mal_id")
                    a_id = doc.get("anilist_id")
                    if m_id:
                        watched_mal_ids.add(str(m_id))
                    if a_id:
                        watched_anilist_ids.add(str(a_id))
            except Exception as e:
                logger.warning("Failed to bulk query id_cache for ID resolving: %s", e)

    filter_watched = user.get("recommendations_filter_watched", True)

    # Sort history to select seed shows
    sorted_user_history = sorted(
        merged_shows.values(),
        key=lambda x: (
            1 if x["status"] in ["completed", "watching"] else 0,
            x["rating"] or 0
        ),
        reverse=True
    )

    # 3. Generate "Top Picks" (Community Recs)
    top_picks = []
    rec_candidates = {}
    
    seed_pool = [s for s in merged_shows.values() if s["status"] in ["completed", "watching", "on_hold"]]
    if not seed_pool:
        seed_pool = [s for s in merged_shows.values() if s["status"] == "planning"]
    if len(seed_pool) > 50:
        seed_pool = random.sample(seed_pool, 50)
        
    sorted_seed_pool = sorted(
        seed_pool,
        key=lambda x: (
            1 if x["status"] in ["completed", "watching"] else 0,
            x["rating"] or 0
        ),
        reverse=True
    )
    recent_seeds = sorted_seed_pool[:5]
    remaining_pool = [s for s in seed_pool if s not in recent_seeds]
    random_seeds = select_weighted_seeds(remaining_pool, 10)
    top_picks_seeds = recent_seeds + random_seeds

    # AniList Bulk query for top picks seeds
    al_ids = [int(s["anilist_id"]) for s in top_picks_seeds if s["anilist_id"]]
    al_recs = []
    anilist_id_to_title = {str(s["anilist_id"]): s["title"] for s in top_picks_seeds if s.get("anilist_id")}
    if al_ids and user.get("anilist_token") and user.get("anilist_enabled", True):
        al_recs = await get_anilist_recommendations_bulk(user["anilist_token"], al_ids)
        
    for rec in al_recs:
        media = rec.get("mediaRecommendation")
        if not media:
            continue
        
        if media.get("status") == "NOT_YET_RELEASED":
            continue
        
        # Exclude OVA, SPECIAL, MUSIC, TV_SHORT and short durations (<= 5 minutes)
        m_format = media.get("format")
        duration = media.get("duration")
        if m_format in ["OVA", "SPECIAL", "MUSIC", "TV_SHORT"]:
            continue
        if duration is not None and duration <= 5:
            continue

        seed_media = rec.get("media") or {}
        seed_aid = str(seed_media.get("id")) if seed_media.get("id") else None
        seed_title = anilist_id_to_title.get(seed_aid) if seed_aid else None

        aid = str(media.get("id"))
        mid = str(media.get("idMal")) if media.get("idMal") else None
        
        # Watched filters
        if filter_watched:
            if aid in watched_anilist_ids or (mid and mid in watched_mal_ids):
                continue

        # Year filter
        year = media.get("startDate", {}).get("year")
        if year and (year < rec_year_min or year > rec_year_max):
            continue

        item_type = "movie" if m_format == "MOVIE" else "series"

        # Excluded genres filter
        genres = media.get("genres", []) or []
        excluded_genres = rec_excluded_movie_genres if item_type == "movie" else rec_excluded_series_genres
        if any(g in excluded_genres for g in genres):
            continue

        # Popularity filters
        pop_score = media.get("popularity") or 0
        avg_score = media.get("averageScore") or 0
        if rec_popularity == "mainstream":
            if pop_score < 25000:
                continue
        elif rec_popularity == "gems":
            if pop_score >= 25000 or avg_score < 73:
                continue

        # Choose title based on language
        title_pref = media.get("title", {})
        if rec_language == "ja":
            title = title_pref.get("romaji") or title_pref.get("userPreferred") or title_pref.get("english")
        else:
            title = title_pref.get("english") or title_pref.get("userPreferred") or title_pref.get("romaji")

        if filter_watched and title.lower() in watched_titles:
            continue

        if not is_proper_anime(title):
            continue

        poster = (media.get("coverImage") or {}).get("large") or (media.get("coverImage") or {}).get("medium") or ""
        syn = clean_html(media.get("description") or "")
        
        key = f"mal:{mid}" if mid else f"anilist:{aid}"
        if key not in rec_candidates:
            rec_candidates[key] = {
                "id": key,
                "type": item_type,
                "name": title,
                "poster": poster,
                "score": rec.get("rating", 1),
                "description": "AniList Community Recommendation.",
                "synopsis": syn,
                "inspired_by_titles": [seed_title] if seed_title else []
            }
        else:
            rec_candidates[key]["score"] += rec.get("rating", 1)
            if syn and not rec_candidates[key].get("synopsis"):
                rec_candidates[key]["synopsis"] = syn
            if seed_title and seed_title not in rec_candidates[key]["inspired_by_titles"]:
                rec_candidates[key]["inspired_by_titles"].append(seed_title)

    # MAL recommendations query for top 5 MAL shows
    mal_seed_shows = [s for s in top_picks_seeds if s["mal_id"]][:5]
    if mal_seed_shows and user.get("mal_access_token") and user.get("mal_enabled", True):
        tasks = [get_mal_recommendations_for_id(user["mal_access_token"], s["mal_id"]) for s in mal_seed_shows]
        mal_recs_lists = await asyncio.gather(*tasks)
        
        for s, rec_list in zip(mal_seed_shows, mal_recs_lists):
            seed_title = s["title"]
            for rec in rec_list:
                node = rec.get("node", {})
                mid = str(node.get("id"))
                title = node.get("title")

                if node.get("status") == "not_yet_aired":
                    continue

                # Exclude OVA, SPECIAL, MUSIC and short duration (<= 5 minutes / 300 seconds)
                m_type = node.get("media_type")
                duration = node.get("average_episode_duration")
                if m_type in ["ova", "special", "music"] or not is_proper_anime(title):
                    continue
                if duration is not None and duration <= 300:
                    continue

                # Watched filters
                if filter_watched:
                    if mid in watched_mal_ids:
                        continue
                    if title.lower() in watched_titles:
                        continue

                # Year filter
                year = node.get("start_season", {}).get("year")
                if year and (year < rec_year_min or year > rec_year_max):
                    continue

                item_type = "movie" if m_type == "movie" else "series"

                # Excluded genres filter
                genres = [g.get("name") for g in node.get("genres", []) if g.get("name")]
                excluded_genres = rec_excluded_movie_genres if item_type == "movie" else rec_excluded_series_genres
                if any(g in excluded_genres for g in genres):
                    continue

                # Popularity filters
                pop_rank = node.get("popularity")
                mean_score = node.get("mean")
                if rec_popularity == "mainstream":
                    if pop_rank and pop_rank > 1200:
                        continue
                elif rec_popularity == "gems":
                    if (pop_rank and pop_rank <= 1200) or (mean_score and mean_score < 7.3):
                        continue

                poster = node.get("main_picture", {}).get("large") or node.get("main_picture", {}).get("medium") or ""
                syn = clean_html(node.get("synopsis") or "")
                
                key = f"mal:{mid}"
                if key not in rec_candidates:
                    rec_candidates[key] = {
                        "id": key,
                        "type": item_type,
                        "name": title,
                        "poster": poster,
                        "score": rec.get("num_recommendations", 1),
                        "description": "MAL Community Recommendation.",
                        "synopsis": syn,
                        "inspired_by_titles": [seed_title]
                    }
                else:
                    rec_candidates[key]["score"] += rec.get("num_recommendations", 1)
                    if syn and not rec_candidates[key].get("synopsis"):
                        rec_candidates[key]["synopsis"] = syn
                    if seed_title not in rec_candidates[key]["inspired_by_titles"]:
                        rec_candidates[key]["inspired_by_titles"].append(seed_title)

    top_picks = sorted(rec_candidates.values(), key=lambda x: x["score"], reverse=True)
    for tp in top_picks:
        tp.pop("score", None)
        syn = tp.get("synopsis") or ""
        inspired_by = tp.get("inspired_by_titles", [])
        if inspired_by:
            desc = f"Inspired by your history: {', '.join(inspired_by)}."
        else:
            desc = tp.get("description") or "Community Recommendation."
        tp["description"] = f"{desc}  \n\n{syn}" if syn else desc

    # 4. Generate "Because you Watched"
    item_recs = []
    seed_show = None
    seed_candidates = [s for s in seed_pool if (s["rating"] or 0) >= 7 or s["status"] in ["completed", "watching"]]
    if not seed_candidates and seed_pool:
        seed_candidates = seed_pool
    if seed_candidates:
        seed_show = random.choice(seed_candidates)
        
    if seed_show:
        item_recs = await get_recommendations_for_seeds([seed_show], user, watched_mal_ids, watched_anilist_ids, watched_titles)
        for ir in item_recs:
            desc = f"Recommended because you watched {seed_show['title']}."
            syn = ir.get("synopsis") or ""
            ir["description"] = f"{desc}  \n\n{syn}" if syn else desc
            
    # Fallback default seeds if empty
    if not item_recs:
        item_recs = []
        for fb in fallbacks:
            if len(item_recs) >= 5:
                break
            
            # Check if watched
            if filter_watched:
                title = fb.get("name", "")
                if title and title.lower() in watched_titles:
                    continue
                fb_id = fb["id"]
                if ":" in fb_id:
                    tracker, ext_id = fb_id.split(":", 1)
                    if tracker == "mal" and ext_id in watched_mal_ids:
                        continue
                    if tracker == "anilist" and ext_id in watched_anilist_ids:
                        continue
                        
            item_copy = fb.copy()
            desc = "Popular trending anime you might enjoy."
            fb_desc = item_copy.get("description") or ""
            item_copy["description"] = f"{desc}  \n\n{fb_desc}" if fb_desc else desc
            item_recs.append(item_copy)
        seed_show = {"title": "Fullmetal Alchemist: Brotherhood"}

    # Filter item_recs for watched shows if filter_watched is True
    if filter_watched and item_recs:
        filtered_item_recs = []
        for ir in item_recs:
            title = ir.get("name", "")
            if title and title.lower() in watched_titles:
                continue
            ir_id = ir.get("id")
            if ir_id and ":" in ir_id:
                tracker, ext_id = ir_id.split(":", 1)
                if tracker == "mal" and ext_id in watched_mal_ids:
                    continue
                if tracker == "anilist" and ext_id in watched_anilist_ids:
                    continue
            filtered_item_recs.append(ir)
        item_recs = filtered_item_recs

    # 5. Generate "Inspired by your Favorites"
    loved_count = 8
    if len(seed_pool) < 16:
        loved_count = max(1, len(seed_pool) // 2)
    loved_seeds = select_weighted_seeds(seed_pool, loved_count)
    loved_items = await get_recommendations_for_seeds(loved_seeds, user, watched_mal_ids, watched_anilist_ids, watched_titles)
    for lr in loved_items:
        inspired_by = lr.get("inspired_by_titles", [])
        if inspired_by:
            desc = f"Inspired by your favorites: {', '.join(inspired_by)}."
        else:
            desc = "Inspired by your favorites."
        syn = lr.get("synopsis") or ""
        lr["description"] = f"{desc}  \n\n{syn}" if syn else desc
    if not loved_items:
        loved_items = []
        for fb in fallbacks[:5]:
            item_copy = fb.copy()
            desc = "Popular trending anime you might enjoy."
            fb_desc = item_copy.get("description") or ""
            item_copy["description"] = f"{desc}  \n\n{fb_desc}" if fb_desc else desc
            loved_items.append(item_copy)

    # 6. Generate "More from your Watchlist"
    remaining_liked_pool = [s for s in seed_pool if s not in loved_seeds]
    liked_count = 8
    if len(seed_pool) < 16:
        liked_count = len(seed_pool) - len(loved_seeds)
    liked_seeds = select_weighted_seeds(remaining_liked_pool, liked_count)
    liked_items = await get_recommendations_for_seeds(liked_seeds, user, watched_mal_ids, watched_anilist_ids, watched_titles)
    for lr in liked_items:
        inspired_by = lr.get("inspired_by_titles", [])
        if inspired_by:
            desc = f"Inspired by your watchlist: {', '.join(inspired_by)}."
        else:
            desc = "More from your watchlist."
        syn = lr.get("synopsis") or ""
        lr["description"] = f"{desc}  \n\n{syn}" if syn else desc
    if not liked_items:
        liked_items = []
        for fb in fallbacks[3:8]:
            item_copy = fb.copy()
            desc = "Popular trending anime you might enjoy."
            fb_desc = item_copy.get("description") or ""
            item_copy["description"] = f"{desc}  \n\n{fb_desc}" if fb_desc else desc
            liked_items.append(item_copy)

    # 7. Genre Collections
    genre_counts = {}
    for show in merged_shows.values():
        if show["status"] == "planning":
            continue
        for g in show.get("genres", []):
            genre_counts[g] = genre_counts.get(g, 0) + 1
    sorted_genres = sorted(genre_counts.items(), key=lambda x: x[1], reverse=True)
    fav_genres = [g[0] for g in sorted_genres[:2]]
    while len(fav_genres) < 2:
        for fallback_genre in ["Action", "Adventure", "Comedy", "Fantasy", "Drama"]:
            if fallback_genre not in fav_genres:
                fav_genres.append(fallback_genre)
                if len(fav_genres) >= 2:
                    break

    genre_1_name = fav_genres[0]
    genre_2_name = fav_genres[1]

    genre_1_items = await generate_genre_recommendations(genre_1_name, user, watched_mal_ids, watched_anilist_ids, watched_titles)
    genre_2_items = await generate_genre_recommendations(genre_2_name, user, watched_mal_ids, watched_anilist_ids, watched_titles)
    if not genre_1_items:
        genre_1_items = []
        for fb in fallbacks[1:6]:
            item_copy = fb.copy()
            desc = "Popular genre collection."
            fb_desc = item_copy.get("description") or ""
            item_copy["description"] = f"{desc}  \n\n{fb_desc}" if fb_desc else desc
            genre_1_items.append(item_copy)
    if not genre_2_items:
        genre_2_items = []
        for fb in fallbacks[2:7]:
            item_copy = fb.copy()
            desc = "Popular genre collection."
            fb_desc = item_copy.get("description") or ""
            item_copy["description"] = f"{desc}  \n\n{fb_desc}" if fb_desc else desc
            genre_2_items.append(item_copy)

    # 8. Enhance recommendations using Gemini API if key is provided
    gemini_api_key = user.get("gemini_api_key", "").strip()
    if gemini_api_key:
        candidates_by_name = {}
        for item in top_picks[:8] + item_recs[:8] + loved_items[:8] + liked_items[:8] + genre_1_items[:8] + genre_2_items[:8]:
            if item.get("name") and item["name"] not in candidates_by_name:
                candidates_by_name[item["name"]] = item

        if candidates_by_name:
            history_lines = []
            for show in sorted_user_history[:15]:
                status = show["status"].lower() if show["status"] else "watched"
                rating_str = f"rated {show['rating']}/10" if show["rating"] else "no rating"
                history_lines.append(f"- {show['title']} ({status}, {rating_str})")
            history_text = "\n".join(history_lines)
            candidates_text = "\n".join([f"- {name}" for name in candidates_by_name.keys()])
            
            prompt = f"""
            You are an advanced anime recommendation assistant.
            Based on the user's anime watch history:
            {history_text}
            
            And this list of candidate anime recommendations:
            {candidates_text}
            
            For each candidate that is relevant, write a personalized, engaging 1-sentence description explaining why the user would like it based on their history (referencing specific anime they watched when appropriate). Keep descriptions concise (under 150 characters).
            
            Return your response as a JSON object mapping the exact candidate title to its personalized description:
            {{
              "Anime Title 1": "Description...",
              "Anime Title 2": "Description...",
              ...
            }}
            Return only the raw JSON.
            """
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_api_key}"
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "responseMimeType": "application/json"
                    }
                }
                client = get_client()
                resp = await client.post(url, json=payload, timeout=15)
                if resp.status_code == 200:
                    res_json = resp.json()
                    text = res_json["candidates"][0]["content"]["parts"][0]["text"]
                    ai_explanations = json.loads(text)
                    
                    def enhance_list(items):
                        enhanced = []
                        others = []
                        for item in items:
                            name = item.get("name")
                            if name in ai_explanations:
                                item_copy = item.copy()
                                ai_desc = ai_explanations[name]
                                syn = item_copy.get("synopsis") or ""
                                item_copy["description"] = f"{ai_desc}  \n\n{syn}" if syn else ai_desc
                                enhanced.append(item_copy)
                            else:
                                others.append(item)
                        return enhanced + others

                    top_picks = enhance_list(top_picks)
                    item_recs = enhance_list(item_recs)
                    loved_items = enhance_list(loved_items)
                    liked_items = enhance_list(liked_items)
                    genre_1_items = enhance_list(genre_1_items)
                    genre_2_items = enhance_list(genre_2_items)
                else:
                    logger.warning("Gemini API call failed with status %s: %s", resp.status_code, resp.text)
            except Exception as e:
                logger.warning("Failed to enhance recommendations with Gemini: %s", e)

    # Deduplicate and pad lists to prevent identical listings across rows
    shown_ids = set()
    watched_titles_filter = watched_titles if filter_watched else set()

    # Helper function to pad a list with popular unique/unwatched anime up to a minimum count
    def pad_catalog(items, fallback_list, shown_ids_set, watched_titles_set, min_count=15, default_desc=None):
        padded_items = []
        for item in items:
            if watched_titles_set:
                title = item.get("name", "")
                if title and title.lower() in watched_titles_set:
                    continue
                item_id = item.get("id")
                if item_id and ":" in item_id:
                    tracker, ext_id = item_id.split(":", 1)
                    if tracker == "mal" and ext_id in watched_mal_ids:
                        continue
                    if tracker == "anilist" and ext_id in watched_anilist_ids:
                        continue
            if item["id"] not in shown_ids_set:
                shown_ids_set.add(item["id"])
                padded_items.append(item)
        for fb_item in fallback_list:
            if len(padded_items) >= min_count:
                break
            if fb_item["id"] in shown_ids_set:
                continue
            title = fb_item.get("name", "")
            if title and title.lower() in watched_titles_set:
                continue
            fb_id = fb_item["id"]
            if ":" in fb_id:
                tracker, ext_id = fb_id.split(":", 1)
                if tracker == "mal" and ext_id in watched_mal_ids:
                    continue
                if tracker == "anilist" and ext_id in watched_anilist_ids:
                    continue
            shown_ids_set.add(fb_item["id"])
            item_copy = fb_item.copy()
            if default_desc:
                fb_desc = item_copy.get("description") or ""
                if fb_desc:
                    item_copy["description"] = f"{default_desc}  \n\n{fb_desc}"
                else:
                    item_copy["description"] = default_desc
            padded_items.append(item_copy)

        # Second pass safety fallback (allow reuse of shown_ids across catalogs if we could not satisfy min_count)
        if len(padded_items) < min_count:
            for fb_item in fallback_list:
                if len(padded_items) >= min_count:
                    break
                # Avoid duplicate within the same row
                if any(x["id"] == fb_item["id"] for x in padded_items):
                    continue
                title = fb_item.get("name", "")
                if title and title.lower() in watched_titles_set:
                    continue
                fb_id = fb_item["id"]
                if ":" in fb_id:
                    tracker, ext_id = fb_id.split(":", 1)
                    if tracker == "mal" and ext_id in watched_mal_ids:
                        continue
                    if tracker == "anilist" and ext_id in watched_anilist_ids:
                        continue
                item_copy = fb_item.copy()
                if default_desc:
                    fb_desc = item_copy.get("description") or ""
                    if fb_desc:
                        item_copy["description"] = f"{default_desc}  \n\n{fb_desc}"
                    else:
                        item_copy["description"] = default_desc
                padded_items.append(item_copy)
        return padded_items

    # 1. Deduplicate & pad Top Picks
    top_picks = pad_catalog(top_picks, fallbacks, shown_ids, watched_titles_filter, min_count=15)
    # 2. Deduplicate & pad Loved Items
    loved_items = pad_catalog(loved_items, fallbacks, shown_ids, watched_titles_filter, min_count=15, default_desc="Popular trending anime you might enjoy.")
    # 3. Deduplicate & pad Liked Items
    liked_items = pad_catalog(liked_items, fallbacks, shown_ids, watched_titles_filter, min_count=15, default_desc="Popular trending anime you might enjoy.")
    # 4. Deduplicate & pad Genre Items
    genre_1_items = pad_catalog(genre_1_items, fallbacks, shown_ids, watched_titles_filter, min_count=15, default_desc="Popular genre collection.")
    genre_2_items = pad_catalog(genre_2_items, fallbacks, shown_ids, watched_titles_filter, min_count=15, default_desc="Popular genre collection.")

    # Enforce sorting order preference (Default, Series First, Movies First)
    def apply_sorting_order(metas):
        if rec_sorting_order == "series_first":
            return sorted(metas, key=lambda x: 0 if x.get("type") == "series" else 1)
        elif rec_sorting_order == "movies_first":
            return sorted(metas, key=lambda x: 0 if x.get("type") == "movie" else 1)
        return metas

    top_picks = apply_sorting_order(top_picks)[:30]
    item_recs = apply_sorting_order(item_recs)[:30]
    loved_items = apply_sorting_order(loved_items)[:30]
    liked_items = apply_sorting_order(liked_items)[:30]
    genre_1_items = apply_sorting_order(genre_1_items)[:30]
    genre_2_items = apply_sorting_order(genre_2_items)[:30]

    # Save to database
    recommendations_cache_collection.update_one(
        {"uid": user_id},
        {
            "$set": {
                "uid": user_id,
                "rec_items": top_picks,
                "item_items": item_recs,
                "item_seed_title": seed_show["title"] if seed_show else "Steins;Gate",
                "loved_items": loved_items,
                "liked_items": liked_items,
                "genre_1_items": genre_1_items,
                "genre_1_name": genre_1_name,
                "genre_2_items": genre_2_items,
                "genre_2_name": genre_2_name,
                "last_updated": datetime.datetime.utcnow()
            }
        },
        upsert=True
    )
    logger.info("Successfully updated recommendations cache for user %s", user_id)


def get_cached_recommendations(user_id: str) -> Optional[dict]:
    return recommendations_cache_collection.find_one({"uid": user_id})


def trigger_recommendation_update_background(user_id: str, force: bool = False):
    user = get_user(user_id)
    if not user or not user.get("enable_recommendations", True):
        return
    asyncio.create_task(update_recommendations_cache(user_id, force=force))


popular_fallbacks_collection = db.get_collection("popular_fallbacks")

def get_popular_fallbacks() -> list[dict]:
    """Retrieve fallback list from database cache, or fallback to the static list if empty."""
    try:
        cached = list(popular_fallbacks_collection.find({}, {"_id": 0}))
        if cached and len(cached) >= 15:
            return cached
    except Exception as e:
        logger.error("Failed to read popular fallbacks from MongoDB: %s", e)
    return POPULAR_FALLBACKS

async def update_popular_fallbacks_cache():
    """Fetch the top 80 most popular anime from AniList and cache them in MongoDB."""
    query = """
    query {
      Page(page: 1, perPage: 80) {
        media(type: ANIME, sort: POPULARITY_DESC) {
          id
          idMal
          status
          format
          duration
          title {
            english
            romaji
            userPreferred
          }
          coverImage {
            large
          }
          description
        }
      }
    }
    """
    try:
        logger.info("Updating popular fallbacks cache from AniList...")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        client = get_client()
        resp = await client.post("https://graphql.anilist.co", json={"query": query}, headers=headers, timeout=15)
        if resp.status_code == 200:
            data = resp.json().get("data", {}).get("Page", {}).get("media", [])
            if data:
                import re
                new_items = []
                for media in data:
                    if media.get("status") == "NOT_YET_RELEASED":
                        continue
                    # Exclude OVA, SPECIAL, MUSIC, TV_SHORT from popular fallbacks and short durations (<= 5 minutes)
                    m_format = media.get("format")
                    duration = media.get("duration")
                    if m_format in ["OVA", "SPECIAL", "MUSIC", "TV_SHORT"]:
                        continue
                    if duration is not None and duration <= 5:
                        continue
                    mal_id = media.get("idMal")
                    item_id = f"mal:{mal_id}" if mal_id else f"anilist:{media.get('id')}"
                    item_type = "movie" if m_format == "MOVIE" else "series"
                    title_pref = media.get("title", {})
                    name = title_pref.get("english") or title_pref.get("userPreferred") or title_pref.get("romaji")
                    if not is_proper_anime(name):
                        continue
                    poster = (media.get("coverImage") or {}).get("large") or ""
                    desc = media.get("description") or ""
                    desc = re.sub('<[^<]+?>', '', desc)
                    desc = desc[:150] + "..." if len(desc) > 150 else desc
                    desc = desc.replace("\n", " ").replace("  ", " ").strip()
                    new_items.append({
                        "id": item_id,
                        "type": item_type,
                        "name": name,
                        "poster": poster,
                        "description": desc
                    })
                if new_items:
                    # Wipe and insert
                    popular_fallbacks_collection.delete_many({})
                    popular_fallbacks_collection.insert_many(new_items)
                    logger.info("Successfully cached %d popular fallbacks from AniList.", len(new_items))
                    return
        logger.warning("Failed to fetch popular fallbacks from AniList: Status %s", resp.status_code)
    except Exception as e:
        logger.error("Failed to update popular fallbacks cache: %s", e)

async def popular_fallbacks_loop():
    """Background loop to update popular fallbacks once every 24 hours."""
    await asyncio.sleep(5)  # Wait for app startup
    while True:
        try:
            await update_popular_fallbacks_cache()
        except Exception as e:
            logger.error("Error in popular fallbacks loop: %s", e)
        await asyncio.sleep(24 * 3600)

def trigger_popular_fallbacks_update_background():
    """Start the background popular fallbacks updater task."""
    asyncio.create_task(popular_fallbacks_loop())
